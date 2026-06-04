import pytest

from app.services.csv_ingestion import CSVValidationError, parse_csv


def test_valid_csv_yields_cleaned_leads() -> None:
    raw = (
        "company_name,industry,contact_email\n"
        "Cascade Modular Homes,Housing,ops@cascade.com\n"
        "Vault Outfitters,Retail,hello@vault.com\n"
    )
    result = parse_csv(raw)
    assert result.total_rows == 2
    assert len(result.valid_leads) == 2
    assert result.errors == []
    assert result.valid_leads[0].company_name == "Cascade Modular Homes"
    assert result.valid_leads[0].industry == "Housing"
    assert result.valid_leads[0].contact_email == "ops@cascade.com"


def test_missing_required_column_raises() -> None:
    raw = "website,industry\nhttp://a.com,Tech\n"
    with pytest.raises(CSVValidationError) as exc:
        parse_csv(raw)
    assert "company_name" in exc.value.message
    assert exc.value.field_name == "company_name"


def test_empty_csv_raises() -> None:
    with pytest.raises(CSVValidationError):
        parse_csv("")


def test_blank_header_raises() -> None:
    with pytest.raises(CSVValidationError):
        parse_csv(",,\n")


def test_extra_columns_preserved_in_cleaned_data() -> None:
    raw = (
        "company_name,linkedin,annual_revenue\n"
        "Cascade,https://linkedin.com/c,12000000\n"
    )
    result = parse_csv(raw)
    assert len(result.valid_leads) == 1
    lead = result.valid_leads[0]
    assert lead.company_name == "Cascade"
    assert lead.cleaned_data == {
        "linkedin": "https://linkedin.com/c",
        "annual_revenue": "12000000",
    }


def test_row_with_empty_company_name_is_invalid() -> None:
    raw = "company_name,industry\n,Tech\nReal Co,Health\n"
    result = parse_csv(raw)
    assert result.total_rows == 2
    assert len(result.valid_leads) == 1
    assert result.valid_leads[0].company_name == "Real Co"
    assert len(result.errors) == 1
    err = result.errors[0]
    assert err.row_number == 2
    assert err.field == "company_name"
    assert "required" in err.message


def test_column_normalization_handles_case_spaces_and_hyphens() -> None:
    raw = (
        " Company Name ,Contact-Email,Contact Title\n"
        "Cascade Modular,ops@cascade.com,VP Operations\n"
    )
    result = parse_csv(raw)
    assert len(result.valid_leads) == 1
    lead = result.valid_leads[0]
    assert lead.company_name == "Cascade Modular"
    assert lead.contact_email == "ops@cascade.com"
    assert lead.contact_title == "VP Operations"
    assert lead.cleaned_data is None  # all columns mapped to known fields


def test_whitespace_trimmed_and_empty_cells_become_null() -> None:
    raw = "company_name,industry,location\n  Cascade  ,  ,USA\n"
    result = parse_csv(raw)
    lead = result.valid_leads[0]
    assert lead.company_name == "Cascade"
    assert lead.industry is None
    assert lead.location == "USA"


def test_blank_rows_are_skipped() -> None:
    raw = "company_name\nA\n\nB\n"
    result = parse_csv(raw)
    assert result.total_rows == 2
    assert [lead.company_name for lead in result.valid_leads] == ["A", "B"]
