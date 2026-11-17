#!/usr/bin/env python3
"""Evidence-first SEC EDGAR U.S. spinoff seeker.  No issuer names are embedded."""
from __future__ import annotations

import argparse, csv, datetime as dt, html, json, os, re, subprocess, sys, time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import certifi
import requests

FORMS = {"10-12B", "10-12G", "10-12B/A", "10-12G/A", "CERT", "8-K"}
REGISTRATION_FORMS = {"10-12B", "10-12G", "10-12B/A", "10-12G/A"}
SEC = "https://www.sec.gov"

def cik10(value: str | int) -> str: return str(value).zfill(10)
def day(value: str) -> dt.date: return dt.date.fromisoformat(value)
def accession_from_path(path: str) -> str: return re.search(r"/(\d{10}-\d{2}-\d{6})\.txt$", path).group(1) if re.search(r"/(\d{10}-\d{2}-\d{6})\.txt$", path) else ""
def clean(text: str) -> str: return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text))).strip()

class SECClient:
    """The only SEC client.  The eight-request limit leaves EDGAR headroom."""
    def __init__(self, user_agent: str):
        if not user_agent: raise ValueError("SEC_USER_AGENT is required in --env-file")
        self.user_agent, self.last = user_agent, 0.0
        self.session=requests.Session()
        self.session.headers.update({"User-Agent":user_agent,"Accept-Encoding":"gzip, deflate","Accept":"*/*"})
    def get(self, url: str) -> bytes:
        pause = .125 - (time.monotonic() - self.last)
        if pause > 0: time.sleep(pause)
        self.last = time.monotonic()
        response=self.session.get(url,timeout=45,verify=certifi.where())
        response.raise_for_status()
        return response.content
    def text(self, url: str) -> str: return self.get(url).decode("utf-8", "replace")
    def json(self, url: str) -> Any: return json.loads(self.text(url))

@dataclass
class Filing:
    cik: str; name: str; form: str; filed: str; path: str
    @property
    def accession(self) -> str: return accession_from_path(self.path)
    @property
    def url(self) -> str: return SEC + self.path

def read_env(path: Path) -> dict[str, str]:
    values = dict(os.environ)
    for raw in path.read_text().splitlines():
        if "=" in raw and not raw.lstrip().startswith("#"):
            key, value = raw.split("=", 1); values[key.strip()] = value.strip().strip('"').strip("'")
    return values

def daily_url(date: dt.date) -> str:
    return f"{SEC}/Archives/edgar/daily-index/{date.year}/QTR{(date.month-1)//3+1}/master.{date:%Y%m%d}.idx"

def quarter_url(year: int, quarter: int) -> str:
    return f"{SEC}/Archives/edgar/full-index/{year}/QTR{quarter}/master.idx"

def quarters_between(start: dt.date, end: dt.date):
    year, quarter=start.year, (start.month-1)//3+1
    while (year,quarter) <= (end.year,(end.month-1)//3+1):
        yield year,quarter
        year,quarter=(year+1,1) if quarter==4 else (year,quarter+1)

def parse_index(text: str) -> list[Filing]:
    rows=[]
    for line in text.splitlines():
        fields=line.split("|")
        if len(fields)==5 and fields[2] in FORMS:
            rows.append(Filing(cik10(fields[0]), fields[1], fields[2], fields[3], "/Archives/"+fields[4]))
    return rows

def index_filings(client: SECClient, start: dt.date, end: dt.date) -> tuple[list[Filing], str]:
    found=[]; latest=""
    for year,quarter in quarters_between(start,end):
        try:
            rows=[row for row in parse_index(client.text(quarter_url(year,quarter))) if start.isoformat() <= row.filed <= end.isoformat()]
            found.extend(rows)
            if rows: latest=max(latest,max(row.filed for row in rows))
        except requests.HTTPError as error:
            if error.response.status_code not in {403,404}: raise
    return found, latest

def folder_documents(client: SECClient, filing: Filing) -> list[tuple[str,str]]:
    acc=filing.accession.replace("-", "")
    if not acc: return []
    base=f"{SEC}/Archives/edgar/data/{int(filing.cik)}/{acc}/"
    try: data=client.json(base+"index.json")
    except (requests.HTTPError, json.JSONDecodeError): return []
    return [(item["name"], base+item["name"]) for item in data.get("directory",{}).get("item",[]) if re.search(r"\.(htm|html|txt)$",item.get("name",""),re.I)]

def source(filing: Filing, section: str) -> dict[str,str]:
    return {"filing":filing.url,"accession":filing.accession,"form":filing.form,"section":section or "Document text"}

def nearby_section(text: str, pos: int) -> str:
    before=text[:pos]
    headings=re.findall(r"(?im)^\s*((?:item\s+)?(?:\d+[A-Z]?|[A-Z][A-Z &,-]{3,})[^\n]{0,110})$", before)
    return clean(headings[-1]) if headings else "Document text"

def match_value(text: str, patterns: list[str]) -> tuple[str,str] | None:
    for pattern in patterns:
        hit=re.search(pattern,text,re.I|re.S)
        if hit: return clean(hit.group(1)), nearby_section(text,hit.start())
    return None

def facts_from_text(text: str, filing: Filing) -> dict[str,dict[str,Any]]:
    result={}
    patterns={
      "parent_company":[r"(?:parent company|our parent)\s*(?:is|,)?\s*([A-Z][A-Za-z0-9 .,&'()-]{2,90})"],
      "distribution_ratio":[r"(?:distribute|distribution of)\s+([0-9.,]+\s+(?:share|shares)[^.;]{0,100}(?:for each|per)[^.;]{0,100})"],
      "record_date":[r"record date\s*(?:will be|is|of|:)\s*([A-Za-z]+,?\s+\d{1,2},?\s+\d{4}|\d{1,2}/\d{1,2}/\d{4})"],
      "distribution_date":[r"(?:distribution date|distributed? (?:on|as of)|distribution (?:will be|is) expected (?:on|to occur on))\s*(?:will be|is|of)?\s*([A-Za-z]+,?\s+\d{1,2},?\s+\d{4}|\d{1,2}/\d{1,2}/\d{4})",
                           r"(?:expected to be|effective)\s+(?:on\s+)?([A-Za-z]+,?\s+\d{1,2},?\s+\d{4}|\d{1,2}/\d{1,2}/\d{4})\s*,?\s*(?:which|that)\s+(?:we|the company)\s+(?:refer to|call)\s+as\s+(?:the\s+)?distribution date"],
      "symbol":[r"(?:trading symbol|ticker symbol|symbol)\s*(?:is|:)?\s*([A-Z]{1,5})\b"],
      "regular_way_start":[r"regular[- ]way trading (?:will |is expected to |did )?begin(?:s|ning)? (?:on )?([A-Za-z]+,?\s+\d{1,2},?\s+\d{4}|\d{1,2}/\d{1,2}/\d{4})",
                           r"begin regular[- ]way trading (?:on )?([A-Za-z]+,?\s+\d{1,2},?\s+\d{4}|\d{1,2}/\d{1,2}/\d{4})"],
      "shares_outstanding":[r"([0-9][0-9,]{2,})\s+shares of [^.]{0,80}?(?:common stock|stock)[^.]{0,80}?outstanding\s+(?:as of|at)\s+([A-Za-z]+\s+\d{1,2},\s+\d{4})",
                            r"(?:as of|at)\s+([A-Za-z]+\s+\d{1,2},\s+\d{4})[^.]{0,120}?([0-9][0-9,]{2,})\s+shares of [^.]{0,80}?outstanding"],
      "insider_ownership_pct":[r"directors and executive officers as a group[^.]{0,600}?([0-9]+(?:\.[0-9]+)?)\s*%"]}
    for key, options in patterns.items():
        if key=="shares_outstanding":
            first=re.search(options[0],text,re.I|re.S); second=None if first else re.search(options[1],text,re.I|re.S)
            hit=first or second
            if hit:
                amount, as_of=(hit.group(1),hit.group(2)) if first else (hit.group(2),hit.group(1))
                result[key]={"value":int(amount.replace(",","")),"date":date_from_value(as_of) or as_of,"source":source(filing,nearby_section(text,hit.start()))}
        else:
            item=match_value(text,options)
            if item: result[key]={"value":item[0],"source":source(filing,item[1])}
    relative=re.search(r"regular[- ]way trading[^.]{0,180}?first trading day (?:following|after) the distribution date",text,re.I|re.S)
    if relative and "regular_way_start" not in result:
        result["regular_way_start"]={"value":"next_trading_day_after_distribution","source":source(filing,nearby_section(text,relative.start()))}
    return result

def date_from_value(value: str) -> str | None:
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%m/%d/%Y"):
        try: return dt.datetime.strptime(value.replace("  "," "),fmt).date().isoformat()
        except ValueError: pass
    return None

def classify_text(text: str) -> str | None:
    lowered=text.lower()
    if re.search(r"\b(?:the registrant|we|the company)\s+(?:is|are)\s+(?:an?\s+)?(?:investment company|mutual fund|exchange[- ]traded fund|closed-end fund)\b",lowered): return "fund"
    if "information statement" not in lowered: return "no information statement"
    if re.search(r"registr(?:ation|ing) .* (?:already outstanding|previously issued|currently held)",lowered): return "registers shares it already has"
    if not re.search(r"\b(parent|distribut\w+|spin[- ]?off)\b",lowered): return "no parent distribution evidence"
    return None

def apply_later_distribution(row: dict[str,Any], facts: dict[str,Any]) -> None:
    """A cited later distribution statement cures only the specified preliminary exclusions."""
    if (row.get("excluded",{}).get("reason") in {"no information statement", "registers shares it already has", "no parent distribution evidence"}
            and row.get("parent_company") and facts.get("distribution_date")):
        row.pop("excluded",None)

def submission_filings(client: SECClient, cik: str) -> list[Filing]:
    data=client.json(f"https://data.sec.gov/submissions/CIK{cik}.json")
    recent=data.get("filings",{}).get("recent",{}); rows=[]
    for i, form in enumerate(recent.get("form",[])):
        if form in {"8-K","10-Q","10-K","4","CERT","DEF 14A"}:
            accession=recent["accessionNumber"][i]; primary=recent.get("primaryDocument",[""]*len(recent["form"]))[i]
            path=f"/Archives/edgar/data/{int(cik)}/{accession.replace('-','')}/{primary}" if primary else ""
            rows.append(Filing(cik, data.get("name",cik), form, recent["filingDate"][i], path))
    return rows

def parent_cik(client: SECClient, parent_name: str) -> str | None:
    """Map an EDGAR disclosed parent name to a CIK only on normalized exact title match."""
    wanted=re.sub(r"[^a-z0-9]", "", parent_name.lower())
    for item in client.json("https://www.sec.gov/files/company_tickers.json").values():
        title=re.sub(r"[^a-z0-9]", "", item.get("title", "").lower())
        if title == wanted: return cik10(item["cik_str"])
    return None

def read_filing_text(client: SECClient, filing: Filing) -> str:
    docs=folder_documents(client,filing); parts=[]
    for _, url in docs[:20]:
        try: parts.append(clean(client.text(url)))
        except requests.HTTPError: pass
    return "\n".join(parts)

def stage(row:dict[str,Any], today:dt.date, window:int) -> str:
    dd=row.get("distribution_date",{}).get("value")
    if dd:
        dist=day(dd)
        if today > dist+dt.timedelta(days=window): return "past its window"
        if today >= dist: return "in its window"
    forms=set(row.get("new_company_forms",[]))
    if forms & {"10-Q","10-K","4"}: return "trading"
    if "CERT" in forms: return "listing approved"
    return "registered"

def price_from_reader(row:dict[str,Any], template:str|None, mode:str) -> dict[str,Any] | None:
    dd=row.get("distribution_date",{}).get("value"); symbol=row.get("symbol",{}).get("value")
    regular_way=row.get("regular_way_start",{}).get("value")
    if not template or not dd or not symbol or not regular_way: return None
    command=template.format(symbol=symbol,cik=row["new_company_cik"],date=dd,regular_way_date=regular_way,mode=mode)
    output=subprocess.run(command,shell=True,text=True,capture_output=True,timeout=60,check=True).stdout
    value=json.loads(output); price_day=day(value["date"])
    if price_day <= day(dd) or price_day < day(regular_way): raise ValueError(f"closes reader returned a non-regular-way date for {row['new_company_cik']}")
    return {"value":float(value["close"]),"date":price_day.isoformat(),"source":value.get("source","closes reader"),"regular_way_source":row["regular_way_start"]["source"]}

def first_price(row:dict[str,Any], template:str|None) -> None:
    value=price_from_reader(row,template,"first")
    if value: row["first_price"]=value

def latest_price(row:dict[str,Any], template:str|None) -> None:
    value=price_from_reader(row,template,"latest")
    if value: row["latest_price"]=value

def derived_factor_values(row: dict[str,Any]) -> dict[str,dict[str,Any]]:
    output={}
    shares, first, latest, insider=(row.get("shares_outstanding"),row.get("first_price"),row.get("latest_price"),row.get("insider_ownership_pct"))
    if shares and latest:
        output["market_cap"]={"value":shares["value"]*latest["value"],"inputs":{"shares_outstanding":shares,"latest_price":latest}}
    if first and latest and first["value"]:
        output["price_return"]={"value":latest["value"]/first["value"]-1,"inputs":{"first_price":first,"latest_price":latest}}
    if insider:
        output["insider_ownership_pct"]={"value":float(insider["value"]),"inputs":{"insider_ownership_pct":insider}}
    return output

def rank(rows:list[dict[str,Any]], config:dict[str,Any], values:dict[str,Any]) -> list[dict[str,Any]]:
    factors=config["factors"]
    for row in rows:
        row["points"]={}; row["total_points"]=0.0
    for factor in factors:
        name, field, weight, direction=factor["name"],factor["field"],float(factor["weight"]),factor["direction"]
        available=[(r,float(values.get(r["new_company_cik"],{}).get(field))) for r in rows if values.get(r["new_company_cik"],{}).get(field) is not None]
        if direction not in {"higher","lower"}: raise ValueError(f"invalid direction for {name}")
        low=min((x[1] for x in available),default=None); high=max((x[1] for x in available),default=None)
        for row in rows:
            raw=values.get(row["new_company_cik"],{}).get(field)
            if raw is None or low is None: points=0.0
            elif high==low: points=abs(weight)
            else: points=abs(weight)*((float(raw)-low)/(high-low) if direction=="higher" else (high-float(raw))/(high-low))
            if weight<0: points=-points
            row["points"][name]={"value":raw,"points":points}; row["total_points"]+=points
    return sorted(rows,key=lambda r:r["total_points"],reverse=True)

def report(rows:list[dict[str,Any]], now:dt.datetime, index_date:str, path:Path) -> None:
    price_dates=sorted({r.get("first_price",{}).get("date","") for r in rows if r.get("first_price")})
    out=[f"Run date: {now.isoformat(timespec='seconds')}; price date: {', '.join(price_dates) or 'none'}; index date: {index_date or 'none'}", "", "# Names in their window"]
    active=[r for r in rows if r.get("stage")=="in its window" and not r.get("excluded")]
    for i,r in enumerate(sorted(active,key=lambda x:x.get("total_points",-float('inf')),reverse=True),1):
        if r.get("unranked_reason"): out.append(f"{i}. {r['new_company_name']} — unranked: {r['unranked_reason']}")
        else: out.append(f"{i}. {r['new_company_name']} — {r.get('total_points',0):.4f} points; " + "; ".join(f"{k}: {v['value']} / {v['points']:.4f}" for k,v in r.get("points",{}).items()))
    out += ["", "# Watch list"]
    out += [f"- {r['new_company_name']} — {r['stage']}" for r in rows if r.get("stage")=="registered" and not r.get("excluded")] or ["- None"]
    out += ["", "# Excluded"]
    out += [f"- {r['new_company_name']} — {r['excluded']['reason']} ({r['excluded']['source']['filing']}, {r['excluded']['source']['section']})" for r in rows if r.get("excluded")] or ["- None"]
    out += ["", "# What the data cannot show"]
    for r in rows:
        missing=[key for key in ("parent_company","distribution_ratio","record_date","distribution_date") if not r.get(key,{}).get("value")]
        if missing: out.append(f"- {r['new_company_name']}: filings read do not show {', '.join(missing)}.")
        if r.get("stage")=="in its window" and r.get("unranked_reason"): out.append(f"- {r['new_company_name']}: {r['unranked_reason']}.")
    out += ["", "screens, not recommendations"]
    path.write_text("\n".join(out)+"\n")

def main() -> None:
    p=argparse.ArgumentParser(); p.add_argument("--start-date",required=True); p.add_argument("--env-file",default=".env",type=Path); p.add_argument("--output-dir",required=True,type=Path); p.add_argument("--factors",required=True,type=Path); p.add_argument("--factor-values",type=Path); p.add_argument("--closes-reader"); p.add_argument("--end-date")
    a=p.parse_args(); start=day(a.start_date); today=day(a.end_date) if a.end_date else dt.date.today(); config=json.loads(a.factors.read_text())
    if not isinstance(config.get("window_days"),int) or config["window_days"]<=0: raise ValueError("factors.window_days must be a positive integer")
    values=json.loads(a.factor_values.read_text()) if a.factor_values else {}
    client=SECClient(read_env(a.env_file).get("SEC_USER_AGENT","")); all_index,index_date=index_filings(client,start,today)
    candidates={f.cik:f for f in all_index if f.form in REGISTRATION_FORMS}; rows=[]
    for cik, filing in candidates.items():
        text=read_filing_text(client,filing); extracted=facts_from_text(text,filing); exclusion=classify_text(text)
        row={"new_company_cik":cik,"new_company_name":filing.name,"registration":source(filing,"Registration statement"),"new_company_forms":[],**extracted}
        if exclusion: row["excluded"]={"reason":exclusion,"source":source(filing,"Registration statement")}
        # A later distribution statement overrides only the three specified preliminary exclusions.
        related=submission_filings(client,cik)
        row["new_company_forms"]=[f.form for f in related]
        for f in related:
            if f.form in {"8-K","CERT","10-Q","10-K","DEF 14A"}:
                later=facts_from_text(read_filing_text(client,f),f)
                for key,value in later.items(): row.setdefault(key,value)
                apply_later_distribution(row,later)
        parent=row.get("parent_company",{}).get("value")
        if parent:
            pcik=parent_cik(client,parent)
            if pcik:
                row["parent_cik"]=pcik
                for f in submission_filings(client,pcik):
                    if f.form=="8-K" and f.filed >= filing.filed:
                        later=facts_from_text(read_filing_text(client,f),f)
                        for key in ("distribution_ratio","record_date","distribution_date"):
                            if key in later: row[key]=later[key]
                        apply_later_distribution(row,later)
        if parent and row.get("distribution_date") and row.get("distribution_date",{}).get("value"):
            row["distribution_date"]["value"]=date_from_value(row["distribution_date"]["value"]) or row["distribution_date"]["value"]
        if row.get("record_date",{}).get("value"): row["record_date"]["value"]=date_from_value(row["record_date"]["value"]) or row["record_date"]["value"]
        if row.get("regular_way_start",{}).get("value") == "next_trading_day_after_distribution" and row.get("distribution_date",{}).get("value"):
            row["regular_way_start"]["value"]=(day(row["distribution_date"]["value"])+dt.timedelta(days=1)).isoformat()
            row["regular_way_start"]["derived_from"]="first trading day after cited distribution date"
        elif row.get("regular_way_start",{}).get("value"):
            row["regular_way_start"]["value"]=date_from_value(row["regular_way_start"]["value"]) or row["regular_way_start"]["value"]
        # Facts from a parent 8-K require a CIK. Parent identity is not guessed from its name.
        row["stage"]=stage(row,today,config["window_days"])
        if not row.get("excluded") and row["stage"] in {"in its window","past its window"}:
            first_price(row,a.closes_reader); latest_price(row,a.closes_reader)
        row["factor_values"]=derived_factor_values(row)
        if row["stage"]=="in its window":
            if not row.get("distribution_date",{}).get("value"): row["unranked_reason"]="distribution date is blank"
            elif not row.get("regular_way_start",{}).get("value"): row["unranked_reason"]="filings do not show a regular-way trading start date"
            elif not row.get("first_price"): row["unranked_reason"]="first regular-way closing price is blank"
        rows.append(row)
    a.output_dir.mkdir(parents=True,exist_ok=True); (a.output_dir/"registry.json").write_text(json.dumps(rows,indent=2)+"\n")
    generated={r["new_company_cik"]:{key:value["value"] for key,value in r.get("factor_values",{}).items()} for r in rows}
    for cik, supplied in values.items(): generated.setdefault(cik,{}).update(supplied)
    (a.output_dir/"factor_values.json").write_text(json.dumps(generated,indent=2)+"\n")
    ranked=rank([r for r in rows if r.get("stage")=="in its window" and not r.get("unranked_reason") and not r.get("excluded")],config,generated)
    by_cik={r["new_company_cik"]:r for r in ranked}
    for r in rows:
        if r["new_company_cik"] in by_cik: r.update(by_cik[r["new_company_cik"]])
    (a.output_dir/"registry.json").write_text(json.dumps(rows,indent=2)+"\n")
    stamp=dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S"); report(rows,dt.datetime.now(),index_date,a.output_dir/f"report_{stamp}.md")

if __name__ == "__main__": main()
