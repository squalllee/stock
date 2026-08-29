from __future__ import annotations

from stock_master.providers.insider import (
    InsiderTransferProvider,
    InsiderUntransferredProvider,
)


class _JsonClient:
    def __init__(self, payload):
        self.payload = payload
        self.urls = []

    def get_json(self, url):
        self.urls.append(url)
        return self.payload


def test_insider_transfer_provider_normalizes_and_filters_stock_codes():
    client = _JsonClient(
        [
            {
                "出表日期": "1150828",
                "公司代號": "2330",
                "公司名稱": "台積電",
                "申請人身分": "董事",
                "姓名": "王小明",
                "預定轉讓方式及股數-轉讓方式": "一般交易",
                "預定轉讓方式及股數-轉讓股數": "12,000",
                "目前持有股數-自有持股": "100,000",
                "目前持有股數-保留運用決定權信託股數": "1,000",
                "預定轉讓總股數-自有持股": "12,000",
                "預定轉讓總股數-保留運用決定權信託股數": "0",
                "預定轉讓後持股-自有持股": "88,000",
                "預定轉讓後持股-保留運用決定權信託股數": "1,000",
                "有效轉讓期間": "115/08/28~115/09/26",
            },
            {
                "出表日期": "1150828",
                "公司代號": "9999",
                "公司名稱": "不在主檔",
                "申請人身分": "董事",
                "姓名": "忽略",
                "預定轉讓方式及股數-轉讓股數": "1,000",
            },
            {"出表日期": "1150828", "公司代號": "", "姓名": ""},
        ]
    )

    provider = InsiderTransferProvider(client, market="TWSE")
    records = provider.fetch({"2330"})

    assert len(records) == 1
    record = records[0]
    assert record.report_date == "2026-08-28"
    assert record.stock_code == "2330"
    assert record.market == "TWSE"
    assert record.report_type == "planned_transfer"
    assert record.transaction_type == "transfer"
    assert record.shares_changed == 12_000
    assert record.current_shares == 101_000
    assert record.planned_shares == 12_000
    assert record.after_shares == 89_000
    assert record.source == "twse_openapi"
    assert len(record.source_record_key) == 64
    assert provider.last_report_date == "2026-08-28"
    assert provider.last_raw_record_count == 3


def test_insider_untransferred_provider_uses_untransferred_shares():
    client = _JsonClient(
        [
            {
                "出表日期": "1150828",
                "公司代號": "3105",
                "姓名": "李小華",
                "申請人身分": "經理人",
                "未轉讓股數-自有持股": "2,000",
                "未轉讓股數-保留運用決定權信託股數": "300",
                "目前持股-自有持股": "8,000",
                "目前持股-保留運用決定權信託股數": "300",
                "原申報預定轉讓股數-自有持股": "5,000",
                "原申報預定轉讓股數-保留運用決定權信託股數": "0",
                "未轉讓理由": "價格不佳",
            }
        ]
    )

    provider = InsiderUntransferredProvider(client, market="TPEX")
    records = provider.fetch({"3105"})

    assert len(records) == 1
    record = records[0]
    assert record.report_type == "untransferred"
    assert record.transaction_type == "untransferred"
    assert record.shares_changed == 2_300
    assert record.current_shares == 8_300
    assert record.planned_shares == 5_000
    assert record.reason == "價格不佳"
    assert record.source == "tpex_openapi"


def test_insider_provider_reads_latest_date_from_placeholder_row():
    client = _JsonClient([{"出表日期": "1150828", "公司代號": "", "姓名": ""}])
    provider = InsiderTransferProvider(client, market="TWSE")

    assert provider.fetch_latest_data_date() == "2026-08-28"
    assert provider.last_raw_record_count == 1


def test_insider_provider_supports_tpex_openapi_field_names():
    client = _JsonClient(
        [
            {
                "Date": "1150828",
                "SecuritiesCompanyCode": "3105",
                "CompanyName": "穩懋",
                "申請人身分": "董事",
                "姓名": "李小華",
                "預定轉讓方式及股數-轉讓方式": "一般交易",
                "預定轉讓方式及股數-轉讓股數": "3,000",
            }
        ]
    )
    provider = InsiderTransferProvider(client, market="TPEX")

    records = provider.fetch({"3105"})

    assert len(records) == 1
    assert records[0].report_date == "2026-08-28"
    assert records[0].stock_code == "3105"
    assert records[0].insider_name == "李小華"
    assert records[0].shares_changed == 3_000


def test_insider_transfer_uses_planned_total_when_method_field_is_concatenated():
    client = _JsonClient(
        [
            {
                "出表日期": "1150828",
                "公司代號": "3189",
                "公司名稱": "景碩",
                "申報人身分": "大股東本人",
                "姓名": "華瑋投資股份有限公司",
                "預定轉讓方式及股數-轉讓方式": "一般交易 鉅額逐筆交易",
                "預定轉讓方式及股數-轉讓股數": "80000008000000",
                "目前持有股數-自有持股": "67037104",
                "預定轉讓總股數-自有持股": "16000000",
                "預定轉讓後持股-自有持股": "51037104",
            }
        ]
    )
    provider = InsiderTransferProvider(client, market="TWSE")

    records = provider.fetch({"3189"})

    assert records[0].shares_changed == 16_000_000
