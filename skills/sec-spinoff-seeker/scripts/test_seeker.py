import importlib.util, json, sys, tempfile, unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

SPEC=importlib.util.spec_from_file_location("seeker",Path(__file__).with_name("seeker.py"))
seeker=importlib.util.module_from_spec(SPEC); sys.modules["seeker"]=seeker; SPEC.loader.exec_module(seeker)

class Rules(unittest.TestCase):
    def filing(self, form="10-12B"):
        return seeker.Filing("0000000001","Registrant A",form,"2025-01-02","/Archives/edgar/data/1/0000000001-25-000001.txt")

    def test_index_reads_all_required_forms(self):
        text="CIK|Company Name|Form Type|Date Filed|Filename\n1|Registrant A|10-12B|2025-01-02|edgar/data/1/0000000001-25-000001.txt\n2|Issuer B|CERT|2025-01-02|edgar/data/2/0000000002-25-000001.txt\n3|Issuer C|8-K|2025-01-02|edgar/data/3/0000000003-25-000001.txt\n"
        self.assertEqual([x.form for x in seeker.parse_index(text)],["10-12B","CERT","8-K"])

    def test_index_uses_quarterly_master_indexes_for_a_weekend_range(self):
        class Client:
            def __init__(self): self.urls=[]
            def text(self,url): self.urls.append(url); return ""
        client=Client(); seeker.index_filings(client,date(2025,11,15),date(2025,11,17))
        self.assertEqual(len(client.urls),1)
        self.assertIn("full-index/2025/QTR4/master.idx",client.urls[0])

    def test_index_skips_a_non_published_holiday_index(self):
        response=seeker.requests.Response(); response.status_code=403
        class Client:
            def text(self,url): raise seeker.requests.HTTPError(response=response)
        filings, latest=seeker.index_filings(Client(),date(2025,11,27),date(2025,11,27))
        self.assertEqual((filings,latest),([],""))

    def test_sec_client_uses_a_certificate_bundle(self):
        client=seeker.SECClient("agent example@example.com")
        with patch.object(client.session,"get") as get:
            response=get.return_value; response.content=b"ok"
            self.assertEqual(client.get("https://example.invalid"),b"ok")
            self.assertTrue(get.call_args.kwargs["verify"])

    def test_registration_exclusions_are_specific(self):
        self.assertEqual(seeker.classify_text("Information Statement. The registrant is an investment company."),"fund")
        self.assertIsNone(seeker.classify_text("Information Statement. The business has an investment company customer. Parent distributes."))
        self.assertEqual(seeker.classify_text("This is a parent distribution."),"no information statement")
        self.assertEqual(seeker.classify_text("Information Statement. Registration of shares already outstanding. Parent distributes."),"registers shares it already has")
        self.assertEqual(seeker.classify_text("Information Statement with ordinary registration text."),"no parent distribution evidence")

    def test_later_parent_distribution_overrides_preliminary_exclusion_only(self):
        row={"excluded":{"reason":"no information statement"},"parent_company":{"value":"Parent Entity"}}
        seeker.apply_later_distribution(row,{"distribution_date":{"value":"January 5, 2025"}})
        self.assertNotIn("excluded",row)
        fund={"excluded":{"reason":"fund"},"parent_company":{"value":"Parent Entity"}}
        seeker.apply_later_distribution(fund,{"distribution_date":{"value":"January 5, 2025"}})
        self.assertIn("excluded",fund)

    def test_facts_keep_value_and_filing_section(self):
        facts=seeker.facts_from_text("ITEM 1. Distribution Details\nOur parent company is Parent Entity. The record date is January 3, 2025. Distribution date is January 5, 2025.",self.filing())
        self.assertEqual(facts["record_date"]["value"],"January 3, 2025")
        self.assertEqual(facts["distribution_date"]["source"]["form"],"10-12B")
        self.assertEqual(facts["parent_company"]["source"]["section"],"ITEM 1. Distribution Details")

    def test_regular_way_start_is_cited_and_required_for_a_price(self):
        facts=seeker.facts_from_text("ITEM 2. Market Information\nRegular-way trading will begin on January 6, 2025.",self.filing())
        self.assertEqual(facts["regular_way_start"]["value"],"January 6, 2025")
        row={"new_company_cik":"0000000001","symbol":{"value":"ABC"},"distribution_date":{"value":"2025-01-03"}}
        with patch("subprocess.run") as run:
            seeker.first_price(row,"reader {symbol} {date} {regular_way_date}")
            run.assert_not_called()

    def test_distribution_date_and_regular_way_relation_from_information_statement(self):
        text=("The reorganization is expected to be on June 1, 2026, which we refer to as the Distribution Date. "
              "We expect regular-way trading to begin on the first trading day following the Distribution Date.")
        facts=seeker.facts_from_text(text,self.filing())
        self.assertEqual(facts["distribution_date"]["value"],"June 1, 2026")
        self.assertEqual(facts["regular_way_start"]["value"],"next_trading_day_after_distribution")

    def test_stage_order(self):
        base={"new_company_forms":[]}
        self.assertEqual(seeker.stage(base,date(2025,2,1),10),"registered")
        self.assertEqual(seeker.stage({"new_company_forms":["CERT"]},date(2025,2,1),10),"listing approved")
        self.assertEqual(seeker.stage({"new_company_forms":["10-Q"]},date(2025,2,1),10),"trading")
        self.assertEqual(seeker.stage({"new_company_forms":["10-Q"],"distribution_date":{"value":"2025-01-30"}},date(2025,2,1),10),"in its window")
        self.assertEqual(seeker.stage({"new_company_forms":["10-Q"],"distribution_date":{"value":"2025-01-01"}},date(2025,2,1),10),"past its window")

    def test_first_price_rejects_when_issued_or_same_day(self):
        row={"new_company_cik":"0000000001","symbol":{"value":"ABC"},"distribution_date":{"value":"2025-01-03"},"regular_way_start":{"value":"2025-01-03"}}
        with patch("subprocess.run") as run:
            run.return_value.stdout=json.dumps({"date":"2025-01-03","close":1})
            with self.assertRaises(ValueError): seeker.first_price(row,"reader {symbol} {date} {regular_way_date}")

    def test_ranking_obeys_direction_weight_and_missing_value(self):
        rows=[{"new_company_cik":"0000000001"},{"new_company_cik":"0000000002"},{"new_company_cik":"0000000003"}]
        ranked=seeker.rank(rows,{"factors":[{"name":"Factor","field":"x","weight":10,"direction":"higher"}]},{"0000000001":{"x":2},"0000000002":{"x":4}})
        self.assertEqual(ranked[0]["new_company_cik"],"0000000002")
        self.assertEqual(ranked[1]["points"]["Factor"]["points"],0)
        self.assertEqual(ranked[2]["points"]["Factor"]["points"],0)

    def test_factor_values_keep_filing_and_price_inputs_and_are_recomputable(self):
        row={"shares_outstanding":{"value":100,"date":"2025-01-01","source":{"filing":"filing-url"}},"insider_ownership_pct":{"value":4.5,"source":{"filing":"filing-url"}},"first_price":{"value":10,"date":"2025-01-04"},"latest_price":{"value":12,"date":"2025-02-01"}}
        values=seeker.derived_factor_values(row)
        self.assertEqual(values["market_cap"]["value"],1200)
        self.assertAlmostEqual(values["price_return"]["value"],.2)
        self.assertEqual(values["insider_ownership_pct"]["value"],4.5)
        self.assertEqual(values["market_cap"]["inputs"]["latest_price"]["date"],"2025-02-01")

    def test_share_count_parser_keeps_number_and_as_of_date_in_each_word_order(self):
        first=seeker.facts_from_text("2,000 shares of common stock outstanding as of January 2, 2025.",self.filing())
        second=seeker.facts_from_text("As of January 2, 2025, 2,000 shares of common stock were outstanding.",self.filing())
        self.assertEqual(first["shares_outstanding"]["value"],2000)
        self.assertEqual(second["shares_outstanding"]["date"],"2025-01-02")

    def test_zeroing_one_weight_does_not_change_other_factor_points(self):
        rows=[{"new_company_cik":"0000000001"},{"new_company_cik":"0000000002"}]
        values={"0000000001":{"a":2,"b":3},"0000000002":{"a":4,"b":6}}
        original=seeker.rank(rows,{"factors":[{"name":"A","field":"a","weight":40,"direction":"higher"},{"name":"B","field":"b","weight":35,"direction":"higher"}]},values)
        zeroed=seeker.rank(rows,{"factors":[{"name":"A","field":"a","weight":40,"direction":"higher"},{"name":"B","field":"b","weight":0,"direction":"higher"}]},values)
        self.assertEqual(original[0]["points"]["A"]["points"],zeroed[0]["points"]["A"]["points"])

    def test_lower_direction_scores_a_negative_return_without_inferring_selling(self):
        rows=[{"new_company_cik":"0000000001"},{"new_company_cik":"0000000002"}]
        ranked=seeker.rank(rows,{"factors":[{"name":"Return","field":"r","weight":35,"direction":"lower"}]},{"0000000001":{"r":-.20},"0000000002":{"r":.10}})
        self.assertEqual(ranked[0]["new_company_cik"],"0000000001")
        self.assertEqual(ranked[0]["points"]["Return"]["points"],35)

    def test_report_has_required_final_line_and_blanks(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/"report.md"
            seeker.report([{"new_company_name":"Registrant A","new_company_cik":"0000000001","stage":"registered"}],seeker.dt.datetime(2025,1,4),"2025-01-03",path)
            text=path.read_text(); self.assertTrue(text.endswith("screens, not recommendations\n")); self.assertIn("parent_company",text)

if __name__=="__main__": unittest.main()
