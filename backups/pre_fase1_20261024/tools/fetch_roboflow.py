"""Download authorized original ONNX artifacts with a strict size budget.

No images, credentials, signed artifact URLs or private responses are logged.
"""
import argparse
import hashlib
import os
from pathlib import Path

import requests

from tools.common import ROOT, write_json


def fetch(args):
    key=os.environ.get("ROBOFLOW_API_KEY","")
    if os.name=="nt":
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,"Environment") as registry:
            key=winreg.QueryValueEx(registry,"ROBOFLOW_API_KEY")[0] or key
    if not key:
        raise ValueError("ROBOFLOW_API_KEY is missing")
    if len(args.model.split("/"))!=2 or not args.model.split("/")[1].isdigit():
        raise ValueError("Use model-id/version")
    try:
        response=requests.get("https://api.roboflow.com/ort/"+args.model,
            params={"api_key":key,"device":"counter-vision-local","dynamic":"true"},timeout=(5,20))
        if response.status_code!=200:
            raise ValueError(f"Roboflow metadata HTTP {response.status_code}")
        artifact=response.json()["ort"]
        env_response=requests.get(artifact["environment"],timeout=(5,20))
        if env_response.status_code!=200:
            raise ValueError("Could not retrieve preprocessing metadata")
        env=env_response.json()
        dest=ROOT/"models"/"roboflow"/(args.model.replace("/","_")+".onnx")
        dest.parent.mkdir(parents=True,exist_ok=True)
        if dest.exists():
            raise ValueError("Artifact exists; will not overwrite")
        received=0; sha=hashlib.sha256()
        with requests.get(artifact["model"],stream=True,timeout=(5,30)) as download:
            if download.status_code!=200:
                raise ValueError(f"Weight download HTTP {download.status_code}")
            if int(download.headers.get("Content-Length",0))>250*1024**2:
                raise ValueError("Artifact exceeds 250 MB safety budget; approval required")
            with dest.with_suffix(".onnx.partial").open("xb") as output:
                for chunk in download.iter_content(1024**2):
                    received+=len(chunk)
                    if received>250*1024**2:
                        raise ValueError("Artifact exceeded download budget")
                    output.write(chunk); sha.update(chunk)
        dest.with_suffix(".onnx.partial").replace(dest)
        metadata={"model_id":args.model,"source":"Roboflow authorized artifact API",
            "names":env.get("CLASS_MAP",artifact.get("classes")),
            "preprocessing":env.get("PREPROCESSING",{}),
            "environment_keys":list(env.keys()),"model_type":artifact.get("modelType"),
            "bytes":received,"sha256":sha.hexdigest(),"training_sessions":[]}
        write_json(dest.with_suffix(".onnx.json"),metadata)
        print({"file":str(dest.relative_to(ROOT)),"bytes":received,
               "model_type":metadata["model_type"],"environment_keys":list(env.keys()),
               "preprocessing":metadata["preprocessing"]})
    except requests.RequestException:
        raise RuntimeError("Artifact request failed; credential and URLs suppressed") from None


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("--model",required=True)
    fetch(p.parse_args())
