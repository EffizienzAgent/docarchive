import io

from pypdf import PdfWriter

SELLER = {"name": "Beispiel Software GmbH", "vat": "DE123456789", "tax": "201/113/40209"}
BUYER = {"name": "Muster Handel AG", "vat": "DE987654321", "tax": ""}


def cii(
	number="RE-2026-0042",
	issue="20260310",
	due="20260409",
	type_code="380",
	seller=SELLER,
	buyer=BUYER,
	reference="04011000-12345-34",
	currency="EUR",
):
	def party(tag, p):
		tax = (
			f'<ram:SpecifiedTaxRegistration><ram:ID schemeID="FC">{p["tax"]}</ram:ID></ram:SpecifiedTaxRegistration>'
			if p["tax"]
			else ""
		)
		return f"""<ram:{tag}><ram:Name>{p["name"]}</ram:Name>
			<ram:PostalTradeAddress><ram:PostcodeCode>10115</ram:PostcodeCode><ram:LineOne>Beispielweg 1</ram:LineOne>
			<ram:CityName>Berlin</ram:CityName><ram:CountryID>DE</ram:CountryID></ram:PostalTradeAddress>
			<ram:SpecifiedTaxRegistration><ram:ID schemeID="VA">{p["vat"]}</ram:ID></ram:SpecifiedTaxRegistration>{tax}
		</ram:{tag}>"""

	return f"""<?xml version="1.0" encoding="UTF-8"?>
<rsm:CrossIndustryInvoice xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
	xmlns:ram="urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"
	xmlns:udt="urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100">
	<rsm:ExchangedDocumentContext><ram:GuidelineSpecifiedDocumentContextParameter>
		<ram:ID>urn:cen.eu:en16931:2017#compliant#urn:xeinkauf.de:kosit:xrechnung_3.0</ram:ID>
	</ram:GuidelineSpecifiedDocumentContextParameter></rsm:ExchangedDocumentContext>
	<rsm:ExchangedDocument><ram:ID>{number}</ram:ID><ram:TypeCode>{type_code}</ram:TypeCode>
		<ram:IssueDateTime><udt:DateTimeString format="102">{issue}</udt:DateTimeString></ram:IssueDateTime>
	</rsm:ExchangedDocument>
	<rsm:SupplyChainTradeTransaction>
		<ram:ApplicableHeaderTradeAgreement><ram:BuyerReference>{reference}</ram:BuyerReference>
			{party("SellerTradeParty", seller)}{party("BuyerTradeParty", buyer)}
		</ram:ApplicableHeaderTradeAgreement>
		<ram:ApplicableHeaderTradeSettlement><ram:InvoiceCurrencyCode>{currency}</ram:InvoiceCurrencyCode>
			<ram:SpecifiedTradePaymentTerms><ram:DueDateDateTime><udt:DateTimeString format="102">{due}</udt:DateTimeString></ram:DueDateDateTime></ram:SpecifiedTradePaymentTerms>
			<ram:SpecifiedTradeSettlementHeaderMonetarySummation>
				<ram:LineTotalAmount>100.00</ram:LineTotalAmount>
				<ram:TaxBasisTotalAmount>100.00</ram:TaxBasisTotalAmount>
				<ram:TaxTotalAmount currencyID="USD">21.50</ram:TaxTotalAmount>
				<ram:TaxTotalAmount currencyID="{currency}">19.00</ram:TaxTotalAmount>
				<ram:GrandTotalAmount>119.00</ram:GrandTotalAmount>
				<ram:DuePayableAmount>119.00</ram:DuePayableAmount>
			</ram:SpecifiedTradeSettlementHeaderMonetarySummation>
		</ram:ApplicableHeaderTradeSettlement>
	</rsm:SupplyChainTradeTransaction>
</rsm:CrossIndustryInvoice>""".encode()


def ubl(
	root="Invoice",
	number="UBL-2026-7",
	issue="2026-03-11",
	due="2026-04-10",
	type_code=None,
	seller=SELLER,
	buyer=BUYER,
):
	type_code = type_code or ("381" if root == "CreditNote" else "380")
	due_tag = f"<cbc:DueDate>{due}</cbc:DueDate>" if root == "Invoice" else ""
	payment = (
		""
		if root == "Invoice"
		else f"<cac:PaymentMeans><cbc:PaymentMeansCode>58</cbc:PaymentMeansCode><cbc:PaymentDueDate>{due}</cbc:PaymentDueDate></cac:PaymentMeans>"
	)

	def party(tag, p):
		tax = (
			f"<cac:PartyTaxScheme><cbc:CompanyID>{p['tax']}</cbc:CompanyID><cac:TaxScheme><cbc:ID>FC</cbc:ID></cac:TaxScheme></cac:PartyTaxScheme>"
			if p["tax"]
			else ""
		)
		return f"""<cac:{tag}><cac:Party>
			<cac:PostalAddress><cbc:StreetName>Musterstr. 5</cbc:StreetName><cbc:CityName>Hamburg</cbc:CityName>
				<cbc:PostalZone>20095</cbc:PostalZone><cac:Country><cbc:IdentificationCode>DE</cbc:IdentificationCode></cac:Country></cac:PostalAddress>
			<cac:PartyTaxScheme><cbc:CompanyID>{p["vat"]}</cbc:CompanyID><cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme></cac:PartyTaxScheme>{tax}
			<cac:PartyLegalEntity><cbc:RegistrationName>{p["name"]}</cbc:RegistrationName></cac:PartyLegalEntity>
		</cac:Party></cac:{tag}>"""

	return f"""<?xml version="1.0" encoding="UTF-8"?>
<{root} xmlns="urn:oasis:names:specification:ubl:schema:xsd:{root}-2"
	xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
	xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
	<cbc:CustomizationID>urn:cen.eu:en16931:2017#compliant#urn:xeinkauf.de:kosit:xrechnung_3.0</cbc:CustomizationID>
	<cbc:ID>{number}</cbc:ID><cbc:IssueDate>{issue}</cbc:IssueDate>{due_tag}
	<cbc:{root}TypeCode>{type_code}</cbc:{root}TypeCode>
	<cbc:DocumentCurrencyCode>EUR</cbc:DocumentCurrencyCode><cbc:BuyerReference>991-01234-56</cbc:BuyerReference>
	{party("AccountingSupplierParty", seller)}{party("AccountingCustomerParty", buyer)}{payment}
	<cac:TaxTotal><cbc:TaxAmount currencyID="EUR">38.00</cbc:TaxAmount></cac:TaxTotal>
	<cac:LegalMonetaryTotal><cbc:LineExtensionAmount currencyID="EUR">200.00</cbc:LineExtensionAmount>
		<cbc:TaxExclusiveAmount currencyID="EUR">200.00</cbc:TaxExclusiveAmount>
		<cbc:TaxInclusiveAmount currencyID="EUR">238.00</cbc:TaxInclusiveAmount>
		<cbc:PayableAmount currencyID="EUR">238.00</cbc:PayableAmount></cac:LegalMonetaryTotal>
</{root}>""".encode()


def zugferd(xml=None, name="factur-x.xml"):
	writer = PdfWriter()
	writer.add_blank_page(width=200, height=200)
	writer.add_attachment(name, xml or cii())
	out = io.BytesIO()
	writer.write(out)
	return out.getvalue()
