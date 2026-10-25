"""Credential diagnostics: status codes only; never persist API keys or URLs."""
import json
import os

import requests

from tools.common import ROOT,write_json


def main():
    key=os.environ.get("ROBOFLOW_API_KEY","")
    if os.name=="nt":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,"Environment") as registry:
                key=winreg.QueryValueEx(registry,"ROBOFLOW_API_KEY")[0] or key
        except OSError:
            pass
    report={"configured":bool(key),"key_type_hint":"publishable" if key.startswith("rf_") else "private_or_unknown",
            "image_uploads":0,"endpoints":{}}
    if key:
        for endpoint in ["/","/ort/dey022/1","/ort/carddetection-v1hqz/17"]:
            try:
                response=requests.get("https://api.roboflow.com"+endpoint,
                    params={"api_key":key,"device":"counter-vision-local","dynamic":"true"},
                    timeout=(5,15),allow_redirects=False)
                report["endpoints"][endpoint]=response.status_code
            except requests.RequestException:
                report["endpoints"][endpoint]="connection_failed"
    write_json(ROOT/"reports"/"roboflow_access.json",report)
    print(json.dumps(report))


if __name__=="__main__":
    main()
