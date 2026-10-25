"""Hosted boundary must fail before network transmission when geometry is unsafe."""
import base64
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from lib.logic.models import ModelError, OnnxModel, RoboflowModel
from tools.common import write_json


def test_no_configuration_means_no_upload(tmp_path,monkeypatch):
    monkeypatch.setenv("ROBOFLOW_API_KEY","test-only-not-a-real-key")
    monkeypatch.setenv("BJ_TABLE_CONFIG",str(tmp_path/"missing.json"))
    monkeypatch.setattr("requests.post",lambda *a,**k:pytest.fail("Unmasked image upload"))
    with pytest.raises(ModelError,match="Hosted inference paused"):
        RoboflowModel("dey022",1).predict(np.zeros((100,100,3),np.uint8))


def test_remote_receives_only_masked_roi_and_coords_restore(tmp_path,monkeypatch):
    monkeypatch.setenv("ROBOFLOW_API_KEY","test-only-not-a-real-key")
    cfg=tmp_path/"table.json"
    write_json(cfg,{"host_resolution":[100,100],"roi":[20,20,80,80],
        "geometry_verified":True,"privacy_reviewed":True,"masks":[[0,.5,1,1]]})
    monkeypatch.setenv("BJ_TABLE_CONFIG",str(cfg))
    def post(*a,**kw):
        encoded=np.frombuffer(base64.b64decode(kw["data"]),np.uint8)
        image=cv2.imdecode(encoded,cv2.IMREAD_COLOR)
        assert image.shape==(60,60,3)
        assert image[40:].max()<5
        return SimpleNamespace(status_code=200,json=lambda:{"predictions":[
            {"x":10,"y":12,"width":8,"height":10,"class":"a1","confidence":.9}]})
    monkeypatch.setattr("requests.post",post)
    p=RoboflowModel("dey022",1).predict(np.full((100,100,3),255,np.uint8))[0]
    assert p["cx"]==30 and p["cy"]==32


def test_secret_never_appears_in_authentication_error(tmp_path,monkeypatch):
    secret="test-only-not-a-real-key"
    monkeypatch.setenv("ROBOFLOW_API_KEY",secret)
    cfg=tmp_path/"table.json"
    write_json(cfg,{"host_resolution":[20,20],"roi":[0,0,20,20],"geometry_verified":True,"privacy_reviewed":True,"masks":[]})
    monkeypatch.setenv("BJ_TABLE_CONFIG",str(cfg))
    monkeypatch.setattr("requests.post",lambda *a,**k:SimpleNamespace(status_code=401))
    with pytest.raises(ModelError) as exc:
        RoboflowModel("dey022",1).predict(np.zeros((20,20,3),np.uint8))
    assert secret not in str(exc.value) and "401" in str(exc.value)


def test_onnx_decoder_maps_stretched_coordinates():
    model=OnnxModel.__new__(OnnxModel)
    model._names={0:"A"}
    output=np.zeros((5,20),np.float32)
    output[:,0]=[320,320,64,64,.9]
    pred=model._decode(output,.5,.5,(.5,1),0,0)[0]
    assert pred["cx"]==640 and pred["cy"]==320
    assert pred["width"]==128 and pred["height"]==64


def test_artifact_sidecar_overrides_numeric_names_and_limits_threads(tmp_path,monkeypatch):
    import sys
    output=np.zeros((1,17,30),np.float32)
    output[0,:4,0]=[320,320,64,64]; output[0,4,0]=.9
    captured={}
    class FakeSession:
        def __init__(self,*a,**kw): captured.update(kw)
        def get_inputs(self): return [SimpleNamespace(name="images",shape=[1,3,640,640])]
        def get_modelmeta(self): return SimpleNamespace(custom_metadata_map={"names":"{0: '0'}"})
        def run(self,*a,**kw): return [output]
    backend=SimpleNamespace(SessionOptions=SimpleNamespace,InferenceSession=FakeSession,
        ExecutionMode=SimpleNamespace(ORT_SEQUENTIAL="sequential"))
    monkeypatch.setitem(sys.modules,"onnxruntime",backend)
    weights=tmp_path/"rank.onnx"
    write_json(str(weights)+".json",{"names":{"0":"10"},"preprocessing":{"resize":{"format":"Stretch to"}}})
    model=OnnxModel(weights)
    p=model.predict(np.zeros((100,200,3),np.uint8))[0]
    assert p["class"]=="10" and p["cx"]==100 and p["cy"]==50
    assert captured["providers"]==["CPUExecutionProvider"]
    assert captured["sess_options"].intra_op_num_threads==2


def test_local_rank_setup_does_not_request_a_remote_cutting_card_model(tmp_path,monkeypatch):
    from lib.common import constants
    from lib.logic.models import ModelProvider,ManualShuffleModel
    monkeypatch.setattr(constants,"MODELS_DIR",tmp_path)
    provider=ModelProvider()
    provider._players=SimpleNamespace(hosted=False,name="Local ONNX")
    dealer=provider.dealer_model()
    assert isinstance(dealer,ManualShuffleModel)
    assert dealer.predict(np.zeros((10,10,3),np.uint8))==[]
    assert not provider.has_hosted()
    assert provider._refresh_one(dealer,"dealer_cards.pt","unused",1,print) is dealer
