#!/usr/bin/env python3
"""Re-score a completed seeker registry without changing its filing or price inputs."""
from __future__ import annotations
import argparse, datetime as dt, importlib.util, json, sys
from pathlib import Path

spec=importlib.util.spec_from_file_location("seeker",Path(__file__).with_name("seeker.py"))
seeker=importlib.util.module_from_spec(spec); sys.modules["seeker"]=seeker; spec.loader.exec_module(seeker)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry",required=True,type=Path)
    parser.add_argument("--factor-values",required=True,type=Path)
    parser.add_argument("--factors",required=True,type=Path)
    parser.add_argument("--output-dir",required=True,type=Path)
    parser.add_argument("--index-date",required=True)
    args=parser.parse_args()
    rows=json.loads(args.registry.read_text()); values=json.loads(args.factor_values.read_text()); config=json.loads(args.factors.read_text())
    ranked=seeker.rank([row for row in rows if row.get("stage")=="in its window" and not row.get("unranked_reason") and not row.get("excluded")],config,values)
    by_cik={row["new_company_cik"]:row for row in ranked}
    for row in rows:
        if row["new_company_cik"] in by_cik: row.update(by_cik[row["new_company_cik"]])
    args.output_dir.mkdir(parents=True,exist_ok=True)
    (args.output_dir/"registry.json").write_text(json.dumps(rows,indent=2)+"\n")
    (args.output_dir/"factor_values.json").write_text(json.dumps(values,indent=2)+"\n")
    stamp=dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    seeker.report(rows,dt.datetime.now(),args.index_date,args.output_dir/f"report_{stamp}.md")

if __name__=="__main__": main()
