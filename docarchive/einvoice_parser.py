import io
from decimal import Decimal, InvalidOperation
from xml.etree.ElementTree import ParseError

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException
from pypdf import PdfReader
from pypdf.errors import PyPdfError

MAX_XML_BYTES = 10 * 1024 * 1024
EMBEDDED_NAMES = ("factur-x.xml", "zugferd-invoice.xml", "xrechnung.xml")
CREDIT_NOTE_CODES = {"81", "83", "261", "262", "296", "308", "381", "396", "420", "458", "532"}
NS = {
	"rsm": "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100",
	"ram": "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100",
	"udt": "urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100",
	"inv": "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2",
	"cn": "urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2",
	"cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
	"cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
}
CII_ROOT = f"{{{NS['rsm']}}}CrossIndustryInvoice"
UBL_ROOTS = {f"{{{NS['inv']}}}Invoice": "Invoice", f"{{{NS['cn']}}}CreditNote": "CreditNote"}


def extract(data: bytes, file_name: str = "") -> dict | None:
	if data.startswith(b"%PDF-"):
		return from_pdf(data)
	return from_xml(data)


def from_pdf(data):
	for name, xml in embedded_xml(data):
		result = from_xml(xml)
		if result:
			return {**result, "embedded_file": name, "xml": xml}
	return None


def embedded_xml(data):
	try:
		files = list(PdfReader(io.BytesIO(data)).attachment_list)
	except PyPdfError, ValueError, KeyError, TypeError:
		return
	files.sort(key=lambda f: (f.name or "").lower() not in EMBEDDED_NAMES)
	for file in files:
		if not (file.name or "").lower().endswith(".xml") or (file.size or 0) > MAX_XML_BYTES:
			continue
		try:
			content = file.content
		except PyPdfError, ValueError, KeyError, TypeError:
			continue
		if len(content) <= MAX_XML_BYTES:
			yield file.name, content


def from_xml(data):
	if not data or len(data) > MAX_XML_BYTES or not data.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"<"):
		return None
	try:
		root = ElementTree.fromstring(data, forbid_dtd=True)
	except ParseError, DefusedXmlException, ValueError:
		return None
	if root.tag == CII_ROOT:
		return cii(root)
	if root.tag in UBL_ROOTS:
		return ubl(root, UBL_ROOTS[root.tag])
	return None


def text(node, path):
	found = node.find(path, NS) if node is not None else None
	value = (found.text or "").strip() if found is not None else ""
	return value or None


def amount(node, path, currency=None):
	nodes = node.findall(path, NS) if node is not None else []
	if currency:
		nodes = sorted(nodes, key=lambda n: n.get("currencyID") not in (None, currency))
	for found in nodes:
		try:
			return str(Decimal((found.text or "").strip()))
		except InvalidOperation:
			continue
	return None


def iso_date(value):
	value = (value or "").strip()
	if len(value) == 8 and value.isdigit():
		return f"{value[:4]}-{value[4:6]}-{value[6:]}"
	if len(value) == 10 and value[4] == value[7] == "-":
		return value
	return None


def kind(type_code, root_name="Invoice"):
	return "credit_note" if root_name == "CreditNote" or type_code in CREDIT_NOTE_CODES else "invoice"


def cii_party(node):
	if node is None:
		return None
	registrations = node.findall("ram:SpecifiedTaxRegistration/ram:ID", NS)
	return {
		"name": text(node, "ram:Name"),
		"vat_ids": [r.text.strip() for r in registrations if r.get("schemeID") == "VA" and r.text],
		"tax_numbers": [r.text.strip() for r in registrations if r.get("schemeID") == "FC" and r.text],
		"address": {
			"line1": text(node, "ram:PostalTradeAddress/ram:LineOne"),
			"line2": text(node, "ram:PostalTradeAddress/ram:LineTwo"),
			"postcode": text(node, "ram:PostalTradeAddress/ram:PostcodeCode"),
			"city": text(node, "ram:PostalTradeAddress/ram:CityName"),
			"country": text(node, "ram:PostalTradeAddress/ram:CountryID"),
		},
	}


def cii(root):
	document = root.find("rsm:ExchangedDocument", NS)
	trade = root.find("rsm:SupplyChainTradeTransaction", NS)
	agreement = trade.find("ram:ApplicableHeaderTradeAgreement", NS) if trade is not None else None
	settlement = trade.find("ram:ApplicableHeaderTradeSettlement", NS) if trade is not None else None
	totals = (
		settlement.find("ram:SpecifiedTradeSettlementHeaderMonetarySummation", NS)
		if settlement is not None
		else None
	)
	currency = text(settlement, "ram:InvoiceCurrencyCode")
	type_code = text(document, "ram:TypeCode")
	return {
		"syntax": "CII",
		"profile": text(
			root, "rsm:ExchangedDocumentContext/ram:GuidelineSpecifiedDocumentContextParameter/ram:ID"
		),
		"number": text(document, "ram:ID"),
		"issue_date": iso_date(text(document, "ram:IssueDateTime/udt:DateTimeString")),
		"due_date": iso_date(
			text(settlement, "ram:SpecifiedTradePaymentTerms/ram:DueDateDateTime/udt:DateTimeString")
		),
		"type_code": type_code,
		"kind": kind(type_code),
		"currency": currency,
		"buyer_reference": text(agreement, "ram:BuyerReference"),
		"seller": cii_party(agreement.find("ram:SellerTradeParty", NS) if agreement is not None else None),
		"buyer": cii_party(agreement.find("ram:BuyerTradeParty", NS) if agreement is not None else None),
		"totals": {
			"net": amount(totals, "ram:TaxBasisTotalAmount"),
			"tax": amount(totals, "ram:TaxTotalAmount", currency),
			"gross": amount(totals, "ram:GrandTotalAmount"),
			"due": amount(totals, "ram:DuePayableAmount"),
		},
	}


def ubl_party(node):
	if node is None:
		return None
	schemes = node.findall("cac:PartyTaxScheme", NS)
	vat_ids, tax_numbers = [], []
	for scheme in schemes:
		value = text(scheme, "cbc:CompanyID")
		if value:
			(vat_ids if text(scheme, "cac:TaxScheme/cbc:ID") == "VAT" else tax_numbers).append(value)
	return {
		"name": text(node, "cac:PartyLegalEntity/cbc:RegistrationName")
		or text(node, "cac:PartyName/cbc:Name"),
		"vat_ids": vat_ids,
		"tax_numbers": tax_numbers,
		"address": {
			"line1": text(node, "cac:PostalAddress/cbc:StreetName"),
			"line2": text(node, "cac:PostalAddress/cbc:AdditionalStreetName"),
			"postcode": text(node, "cac:PostalAddress/cbc:PostalZone"),
			"city": text(node, "cac:PostalAddress/cbc:CityName"),
			"country": text(node, "cac:PostalAddress/cac:Country/cbc:IdentificationCode"),
		},
	}


def ubl(root, root_name):
	currency = text(root, "cbc:DocumentCurrencyCode")
	type_code = text(root, f"cbc:{root_name}TypeCode")
	totals = root.find("cac:LegalMonetaryTotal", NS)
	return {
		"syntax": "UBL",
		"profile": text(root, "cbc:CustomizationID"),
		"number": text(root, "cbc:ID"),
		"issue_date": iso_date(text(root, "cbc:IssueDate")),
		"due_date": iso_date(text(root, "cbc:DueDate") or text(root, "cac:PaymentMeans/cbc:PaymentDueDate")),
		"type_code": type_code,
		"kind": kind(type_code, root_name),
		"currency": currency,
		"buyer_reference": text(root, "cbc:BuyerReference"),
		"seller": ubl_party(root.find("cac:AccountingSupplierParty/cac:Party", NS)),
		"buyer": ubl_party(root.find("cac:AccountingCustomerParty/cac:Party", NS)),
		"totals": {
			"net": amount(totals, "cbc:TaxExclusiveAmount"),
			"tax": amount(root, "cac:TaxTotal/cbc:TaxAmount", currency),
			"gross": amount(totals, "cbc:TaxInclusiveAmount"),
			"due": amount(totals, "cbc:PayableAmount"),
		},
	}
