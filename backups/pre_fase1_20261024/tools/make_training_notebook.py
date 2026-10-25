"""Write a Colab notebook; this script does not install or run training locally."""
import json
from tools.common import ROOT


def main():
    cells=[]
    def md(text): cells.append({"cell_type":"markdown","metadata":{},"source":text.splitlines(True)})
    def code(text): cells.append({"cell_type":"code","execution_count":None,"metadata":{},"outputs":[],"source":text.splitlines(True)})
    md("# Rank-only blackjack detector — training outside the user's PC\n"
       "Run in a Colab GPU runtime. Upload only the masked `rank_training.zip` produced by tools.prepare_training. "
       "The holdout folder is never used for fitting or early stopping. No API key is needed. "
       "Export is a candidate; run the offline acceptance harness before promotion.")
    code("import os\nassert 'COLAB_RELEASE_TAG' in os.environ, 'Run this notebook in Colab, not on the laptop'\n"
         "%pip -q install ultralytics onnx\nfrom google.colab import files\nimport zipfile, json, pathlib\n"
         "uploaded = files.upload()\nassert len(uploaded) == 1\narchive = next(iter(uploaded))\n"
         "with zipfile.ZipFile(archive) as z:\n    assert all('..' not in pathlib.PurePosixPath(n).parts and not n.startswith('/') for n in z.namelist())\n    z.extractall('/content/dataset')\n"
         "provenance = json.load(open('/content/dataset/provenance.json'))\n"
         "assert not set(provenance['training_sessions']) & set(provenance['evaluation_sessions'])\n")
    code("from ultralytics import YOLO\nimport torch\nassert torch.cuda.is_available(), 'Select a Colab GPU runtime'\n"
         "model = YOLO('yolo11n.pt')\nresult = model.train(data='/content/dataset/data.yaml', epochs=40, imgsz=640, batch=16, "
         "device=0, workers=2, cache=False, patience=10, seed=17, fliplr=0.0, flipud=0.0, degrees=20, "
         "project='/content/rank_training', name='candidate')\n")
    code("import hashlib, shutil\nbest = YOLO('/content/rank_training/candidate/weights/best.pt')\n"
         "exported = pathlib.Path(best.export(format='onnx', imgsz=640, dynamic=False, simplify=False))\n"
         "output = pathlib.Path('/content/rank_cards.onnx')\nshutil.copyfile(exported, output)\n"
         "metadata = {'names': best.names, 'preprocessing': {}, 'training_sessions': provenance['training_sessions'], "
         "'validation_rounds': provenance['validation_rounds'], 'calibration_sessions': [], "
         "'sha256': hashlib.sha256(output.read_bytes()).hexdigest(), 'acceptance_verified': False}\n"
         "pathlib.Path(str(output)+'.json').write_text(json.dumps(metadata, indent=2))\n"
         "with zipfile.ZipFile('/content/rank_candidate.zip', 'w') as z:\n"
         "    z.write(output, output.name)\n    z.write(str(output)+'.json', output.name+'.json')\n"
         "files.download('/content/rank_candidate.zip')\n")
    md("## Back on Windows\nPlace candidate files under models/ without replacing the active weights. "
       "Evaluate only the separate evaluation session, including temporal labels and cross review. "
       "The 98%/99%/per-rank, counting, live latency and 30-minute soak gates remain mandatory.")
    out=ROOT/"training"/"train_rank_colab.ipynb"; out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"nbformat":4,"nbformat_minor":5,"cells":cells,
        "metadata":{"kernelspec":{"display_name":"Python 3","language":"python","name":"python3"},
                    "colab":{"name":"train_rank_colab.ipynb"},"accelerator":"GPU"}},indent=2),encoding="utf-8")
    print("Wrote Colab notebook; no training was run locally")


if __name__=="__main__": main()
