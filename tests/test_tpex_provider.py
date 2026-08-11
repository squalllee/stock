from stock_master.models import Stock
from stock_master.providers.tpex import TPExStockProvider


class FakeJsonClient:
    def __init__(self, payload):
        self.payload = payload

    def get_json(self, url):
        return self.payload


def test_fetch_tpex_stock_and_exclude_etf():
    client = FakeJsonClient(
        [
            {"公司代號": "3105", "公司簡稱": "穩懋"},
            {"公司代號": "00878", "公司簡稱": "國泰永續高股息"},
            {"公司代號": "123456", "公司簡稱": "示例權證"},
        ]
    )
    stocks = TPExStockProvider(client).fetch()

    assert stocks == [Stock("3105", "穩懋", "TPEX")]


def test_fetch_tpex_current_english_schema():
    client = FakeJsonClient(
        [
            {
                "SecuritiesCompanyCode": "3105",
                "CompanyName": "穩懋科技股份有限公司",
                "CompanyAbbreviation": "穩懋",
            }
        ]
    )

    assert TPExStockProvider(client).fetch() == [
        Stock("3105", "穩懋", "TPEX")
    ]
