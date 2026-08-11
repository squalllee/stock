from stock_master.services.stock_filter import StockFilter


def test_accept_common_stock_with_official_category():
    record = {"證券代號": "2330", "證券名稱": "台積電", "證券類別": "普通股"}
    assert StockFilter().is_common_stock(record)


def test_reject_etf():
    record = {"證券代號": "0050", "證券名稱": "元大台灣50", "商品類別": "ETF"}
    assert not StockFilter().is_common_stock(record)


def test_reject_etn():
    record = {"證券代號": "020001", "證券名稱": "示例", "商品類別": "ETN"}
    assert not StockFilter().is_common_stock(record)


def test_reject_warrant():
    record = {"證券代號": "123456", "證券名稱": "台積電認購權證", "證券類別": "權證"}
    assert not StockFilter().is_common_stock(record)


def test_reject_bond():
    record = {"證券代號": "00701", "證券名稱": "示例公司債", "證券類別": "公司債"}
    assert not StockFilter().is_common_stock(record)


def test_reject_unclassified_records_without_an_official_scope():
    record = {"證券代號": "2330", "證券名稱": "台積電"}
    assert not StockFilter().is_common_stock(record)


def test_company_basic_official_scope_can_fallback_when_category_is_absent():
    record = {
        "公司代號": "2330",
        "公司簡稱": "台積電",
        "_official_dataset": "listed_company_basic",
    }
    assert StockFilter(allow_official_profile_fallback=True).is_common_stock(record)


def test_reject_depositary_receipt_by_name():
    record = {
        "公司代號": "9103",
        "公司簡稱": "明輝-DR",
        "_official_dataset": "listed_company_basic",
    }
    assert not StockFilter(allow_official_profile_fallback=True).is_common_stock(
        record
    )

