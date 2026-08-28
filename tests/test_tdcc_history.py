from datetime import date

from stock_master.main import build_parser
from stock_master.models import TDCCDistribution
from stock_master.providers.tdcc_history import (
    TDCCHistoricalDistributionProvider,
    parse_history_page,
)


INITIAL_HTML = """
<html>
<form>
  <input type="hidden" name="SYNCHRONIZER_TOKEN" value="catalog-token">
  <input type="hidden" name="SYNCHRONIZER_URI" value="/portal/zh/smWeb/qryStock">
  <input type="hidden" name="firDate" value="20260807">
  <select name="scaDate">
    <option value="20260807">20260807</option>
    <option value="20260731">20260731</option>
    <option value="20260724">20260724</option>
    <option value="20260717">20260717</option>
  </select>
</form>
</html>
"""


def result_html(date_code: str, stock_code: str, token: str) -> str:
    current = date.fromisoformat(
        f"{date_code[:4]}-{date_code[4:6]}-{date_code[6:]}"
    )
    roc_year = current.year - 1911
    return f"""
    <html>
      <input type="hidden" name="SYNCHRONIZER_TOKEN" value="{token}">
      <input type="hidden" name="SYNCHRONIZER_URI" value="/portal/zh/smWeb/qryStock">
      <input type="hidden" name="firDate" value="20260807">
      <select name="scaDate">
        <option value="20260807" {'selected' if date_code == '20260807' else ''}>20260807</option>
        <option value="20260731" {'selected' if date_code == '20260731' else ''}>20260731</option>
        <option value="20260724" {'selected' if date_code == '20260724' else ''}>20260724</option>
      </select>
      <p>證券代號：{stock_code}<br>證券名稱：測試公司</p>
      <span>資料日期：{roc_year}年{current.month:02d}月{current.day:02d}日</span>
      <table>
        <thead>
          <tr>
            <th>序</th>
            <th>持股/單位數分級</th>
            <th>人數</th>
            <th>股數/單位數</th>
            <th>占集保庫存數比例 (%)</th>
          </tr>
        </thead>
        <tr><td>1</td><td>1-999</td><td>1,234</td><td>12,345</td><td>12.34</td></tr>
        <tr><td>2</td><td>1,000-5,000</td><td>42</td><td>987,654</td><td>3.21%</td></tr>
        <tr><td>16</td><td>差異數調整（說明4）</td><td></td><td>-10</td><td>-0.00</td></tr>
        <tr><td>17</td><td>合　計</td><td>1,276</td><td>999,999</td><td>100.00</td></tr>
      </table>
    </html>
    """


class FakeHistoryClient:
    def __init__(self):
        self.forms = []

    def get_text(self, url):
        return INITIAL_HTML

    def post_form(self, url, fields):
        self.forms.append(dict(fields))
        token = f"token-{len(self.forms)}"
        return result_html(fields["scaDate"], fields["stockNo"], token)


class MissingSessionFieldsClient(FakeHistoryClient):
    def get_text(self, url):
        return "<html><body>temporary TDCC page</body></html>"


def test_parse_history_page_extracts_rows_and_skips_adjustments_and_total():
    page = parse_history_page(
        result_html("20260731", "2330", "next-token"),
        expected_date="2026-07-31",
        expected_stock_code="2330",
    )

    assert page.available_dates == (
        "2026-08-07",
        "2026-07-31",
        "2026-07-24",
    )
    assert page.records == (
        TDCCDistribution("2026-07-31", "2330", "1", 1234, 12345, 12.34),
        TDCCDistribution("2026-07-31", "2330", "2", 42, 987654, 3.21),
    )
    assert page.skipped_total_count == 1
    assert page.skipped_adjustment_count == 1
    assert page.token == "next-token"


def test_parse_history_page_prefers_selected_date_when_display_year_is_malformed():
    malformed_html = result_html(
        "20260807", "2330", "next-token"
    ).replace("115年08月07日", "5115年08月31日")

    page = parse_history_page(
        malformed_html,
        expected_date="2026-08-07",
        expected_stock_code="2330",
    )

    assert page.selected_dates == ("2026-08-07",)
    assert page.records[0].data_date == "2026-08-07"


def test_parse_history_page_accepts_slash_separated_result_date():
    slash_date_html = result_html(
        "20260807", "2330", "next-token"
    ).replace("115年08月07日", "115/08/07")

    page = parse_history_page(
        slash_date_html,
        expected_date="2026-08-07",
        expected_stock_code="2330",
    )

    assert page.records[0].data_date == "2026-08-07"


def test_parse_history_page_uses_selected_date_when_heading_is_missing():
    missing_heading_html = result_html(
        "20260807", "2330", "next-token"
    ).replace("<span>資料日期：115年08月07日</span>", "")

    page = parse_history_page(
        missing_heading_html,
        expected_date="2026-08-07",
        expected_stock_code="2330",
    )

    assert page.selected_dates == ("2026-08-07",)
    assert page.records[0].data_date == "2026-08-07"


def test_parse_history_page_treats_no_data_variants_as_empty_results():
    no_data_html = result_html("20260807", "2330", "next-token").replace(
        '<span>資料日期：115年08月07日</span>',
        '<span class="font">查無資料</span>',
    )

    page = parse_history_page(
        no_data_html,
        expected_date="2026-08-08",
        expected_stock_code="2330",
    )

    assert page.no_data is True
    assert page.records == ()


def test_parse_history_page_extracts_catalog_without_result_table():
    page = parse_history_page(INITIAL_HTML)

    assert page.first_date == "2026-08-07"
    assert page.available_dates[0] == "2026-08-07"
    assert page.records == ()


def test_historical_provider_filters_dates_and_chains_csrf_tokens():
    clients = []

    def factory():
        client = FakeHistoryClient()
        clients.append(client)
        return client

    provider = TDCCHistoricalDistributionProvider(
        factory,
        days=20,
        end_date=date(2026, 8, 7),
        workers=1,
        request_delay_seconds=0,
        sleep=lambda _: None,
    )

    records = provider.fetch({"2330"})

    assert provider.last_data_dates == (
        "2026-07-24",
        "2026-07-31",
        "2026-08-07",
    )
    assert len(records) == 6
    assert provider.last_request_count == 3
    assert provider.last_empty_query_count == 0
    assert provider.last_skipped_total_count == 3
    assert provider.last_skipped_adjustment_count == 3
    assert len(clients) == 1
    assert [form["scaDate"] for form in clients[0].forms] == [
        "20260724",
        "20260731",
        "20260807",
    ]
    assert clients[0].forms[0]["SYNCHRONIZER_TOKEN"] == "catalog-token"
    assert clients[0].forms[1]["SYNCHRONIZER_TOKEN"] == "token-1"
    assert clients[0].forms[2]["SYNCHRONIZER_TOKEN"] == "token-2"


def test_historical_provider_skips_checkpoints_and_fetches_newest_first():
    client = FakeHistoryClient()
    provider = TDCCHistoricalDistributionProvider(
        client,
        days=20,
        end_date=date(2026, 8, 7),
        workers=1,
        request_delay_seconds=0,
        newest_first=True,
        sleep=lambda _: None,
    )

    records = provider.fetch(
        {"2330"},
        completed_queries={("2026-08-07", "2330")},
    )

    assert [form["scaDate"] for form in client.forms] == [
        "20260731",
        "20260724",
    ]
    assert len(records) == 4
    assert [
        (result.data_date, result.stock_code, result.record_count)
        for result in provider.last_query_results
    ] == [
        ("2026-07-31", "2330", 2),
        ("2026-07-24", "2330", 2),
    ]


def test_historical_provider_refreshes_session_after_incomplete_result_page():
    clients = []

    class FirstResultIsCatalogClient(FakeHistoryClient):
        def post_form(self, url, fields):
            self.forms.append(dict(fields))
            return INITIAL_HTML

    def factory():
        client = (
            FirstResultIsCatalogClient()
            if not clients
            else FakeHistoryClient()
        )
        clients.append(client)
        return client

    provider = TDCCHistoricalDistributionProvider(
        factory,
        days=20,
        end_date=date(2026, 8, 7),
        workers=1,
        request_delay_seconds=0,
        sleep=lambda _: None,
    )

    records = provider.fetch({"2330"})

    assert len(clients) == 2
    assert len(records) == 6
    assert provider.last_request_count == 4
    assert len(provider.last_query_results) == 3


def test_historical_provider_reopens_session_when_catalog_fields_are_missing():
    clients = []
    sleeps = []

    def factory():
        client = MissingSessionFieldsClient() if not clients else FakeHistoryClient()
        clients.append(client)
        return client

    provider = TDCCHistoricalDistributionProvider(
        factory,
        days=20,
        end_date=date(2026, 8, 7),
        workers=1,
        request_delay_seconds=0,
        sleep=sleeps.append,
    )

    dates = provider.available_dates()

    assert dates == ("2026-07-24", "2026-07-31", "2026-08-07")
    assert len(clients) == 2
    assert sleeps == [0.5]


def test_cli_exposes_month_and_history_aliases():
    parser = build_parser()

    args = parser.parse_args(["tdcc-month-sync", "--days", "31"])
    assert args.days == 31
    assert args.workers == 2

    alias_args = parser.parse_args(["tdcc-history-sync"])
    assert alias_args.command == "tdcc-history-sync"
