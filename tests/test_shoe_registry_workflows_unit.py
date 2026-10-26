"""Real engine/memory/counter workflows, using only invented observations."""
from copy import deepcopy
from types import SimpleNamespace
import random
import pytest

from lib.common import constants
from lib.logic.card_layout import CardLayout
from lib.logic.card_memory import CardMemory
from lib.logic.counting import CardCounter
from lib.logic.engine import DetectionEngine
from lib.logic import region_profiles


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setattr(constants, "OUTPUT_DIR", tmp_path)
    monkeypatch.setitem(constants.BETTING, "use_exact_edge", 0)
    for cfg in constants.SIDE_BETS.values():
        monkeypatch.setitem(cfg, "enabled", False)
    for cfg in (constants.OCR, constants.PHASE, constants.ANCHORS, constants.TRAINING):
        monkeypatch.setitem(cfg, "enabled", 0)
    layout = CardLayout.from_payload({"mode":"individual", "resolution":[400,300],
        "mine":[[20,150],[250,150],[250,280],[20,280]],
        "dealer":[[270,20],[390,20],[390,130],[270,130]]}, (400,300))
    region_profiles.set_card_layout(layout, profile="Test")
    provider = SimpleNamespace(backend_name="unit", has_hosted=lambda:False)
    e = DetectionEngine(provider=provider, persist=False, log=lambda *a, **kw:None)
    e.set_monitor(SimpleNamespace(x=0,y=0,width=400,height=300), profile="Test", mode="individual")
    yield e
    for name in ("_pool", "_advice_pool", "_io_pool", "_predeal_pool"):
        getattr(e,name).shutdown(wait=True, cancel_futures=True)


def observe(e, rank="6", x=80., ambiguous=False, repeats=3):
    for i in range(repeats):
        e.card_registry.observe([{"name":rank,"class":rank,"owner":"mine", "confidence":.96,
            "cx":x,"cy":200.,"width":20.,"height":30.,"ambiguous":ambiguous,
            "context":e.correction_context()}], float(i+1))
    e._sync_registry_cards()
    e.publish_snapshot()
    return next(c.id for c in e.card_registry.cards.values() if c.cx==x)


@pytest.mark.parametrize("old,new,delta", [("6","10",-2),("3","5",0),("8","Ace",-1)])
def test_active_corrections_are_atomic_composition_updates_and_idempotent(engine, old, new, delta):
    cid=observe(engine,old)
    before=engine.counter.snapshot()
    result=engine.apply_correction(cid,engine.round_number,"set_rank",new_rank=new)
    after=engine.counter.snapshot()
    assert result.ok and result.rc_delta==delta
    assert after["running"]==before["running"]+delta
    assert after["cards_seen"]==before["cards_seen"]==1
    assert after["true"]==after["running"]/after["decks_remaining"]
    state=engine.counter.get_state()
    assert not engine.apply_correction(cid,engine.round_number,"set_rank",new_rank=new).ok
    assert engine.counter.get_state()==state
    observe(engine,old)
    assert engine.card_registry.cards[cid].rank==new
    assert engine.counter.get_state()==state


def test_history_corrects_same_shoe_after_round_and_rejects_old_live_selection(engine):
    cid=observe(engine,"6")
    context=deepcopy(engine.card_registry.cards[cid].context)
    seen=engine.counter.cards_seen
    engine.new_round()
    assert not engine.card_registry.cards and cid in engine.card_registry.archive
    assert engine.counter.cards_seen==seen
    assert not engine.apply_correction(cid,context["round_id"],"set_rank",new_rank="10").ok
    result=engine.apply_historical_correction(cid,context,"set_rank",new_rank="10")
    assert result.ok and result.rc_delta==-2 and engine.counter.cards_seen==seen
    assert engine.counter.running_count==-1
    assert engine.get_snapshot()["count"]["running"]==-1
    assert engine.card_registry.archive[cid].events[-1]["kind"]=="historical_correction"
    before=engine.counter.get_state()
    assert not engine.apply_historical_correction(cid,context,"set_rank",new_rank="10").ok
    for field in ("shoe_id","round_id","profile_name","mode","resolution","source_id","source_ref"):
        bad=deepcopy(context);bad[field]="stale"
        assert not engine.apply_historical_correction(cid,bad,"set_rank",new_rank="2").ok
    assert engine.counter.get_state()==before
    engine.reset_shoe()
    assert not engine.apply_historical_correction(cid,context,"not_a_card").ok
    assert engine.counter.cards_seen==0


def test_historical_pending_confirm_discard_and_suit_change(engine):
    cid=observe(engine,"8",ambiguous=True)
    context=deepcopy(engine.card_registry.cards[cid].context)
    assert engine.counter.cards_seen==0
    engine.new_round()
    assert engine.apply_historical_correction(cid,context,"set_rank",new_rank="8",new_suit="H").ok
    assert engine.counter.cards_seen==1 and engine.counter.running_count==0
    assert engine.apply_historical_correction(cid,context,"set_rank",new_rank="8",new_suit="S").ok
    assert engine.counter.suit_seen["8 of Hearts"]==0 and engine.counter.suit_seen["8 of Spades"]==1
    assert engine.apply_historical_correction(cid,context,"not_a_card").ok
    assert engine.counter.cards_seen==0 and not engine.counter.count_uncertain
    assert not engine.apply_historical_correction(cid,context,"not_a_card").ok


def test_manual_add_uses_real_bbox_reuses_identity_and_retains_manual_rank(engine):
    context=engine.correction_context()
    result,cid=engine.add_manual_card(context,[70,185,90,215],"10","H")
    assert result.ok and cid and engine.counter.running_count==-1 and engine.counter.cards_seen==1
    assert engine.card_registry.cards[cid].manual_added
    for i in range(8):
        engine.card_registry.observe([{"name":"6","owner":"mine","cx":80.,"cy":200.,
            "width":20.,"height":30.,"confidence":.99}],10.+i)
    assert list(engine.card_registry.cards)==[cid] and engine.card_registry.cards[cid].rank=="10"
    assert engine.counter.running_count==-1 and engine.counter.cards_seen==1
    _,same=engine.add_manual_card(context,[70,185,90,215],"10","H")
    assert same==cid and engine.counter.cards_seen==1
    before=engine.counter.get_state()
    for bbox in ([0,0,10,10],[0,0,float("nan"),10],[-1,10,20,30],[10,20,5,40]):
        assert not engine.add_manual_card(context,bbox,"2")[0].ok
    engine.new_round()
    assert not engine.add_manual_card(context,[70,185,90,215],"2")[0].ok
    assert engine.counter.get_state()==before


def test_archive_and_manual_decisions_survive_restore_without_recount(engine):
    cid=observe(engine,"6")
    context=deepcopy(engine.card_registry.cards[cid].context)
    engine.new_round()
    engine.apply_historical_correction(cid,context,"set_rank",new_rank="10",new_suit="D")
    saved=deepcopy(engine._shoe_state())
    counter=CardCounter();counter.apply_state(saved)
    memory=CardMemory(counter);memory.apply_state(saved["card_memory"])
    assert counter.get_state()=={k:v for k,v in saved.items() if k!="card_memory"}
    assert memory.archive[cid].rank=="10" and memory.archive[cid].manual
    memory.observe([{"name":"2","owner":"mine","cx":80.,"cy":200.,"width":20,"height":30}],100.)
    assert len(set(memory.cards)|set(memory.archive))==2
    assert memory.serial>int(cid.rsplit(":",1)[1])


def test_inspection_explains_confirmation_once_and_keeps_unrelated_uncertainty(engine):
    cid=observe(engine,"6",repeats=1)
    row=engine.get_registry_inspection()["cards"][0]
    assert row["state"]=="pending" and not row["counted"] and row["observations"]==1
    assert row["confidence"]==.96 and row["confirmed_at"] is None
    observe(engine,"6",repeats=30)
    row=engine.get_registry_inspection()["cards"][0]
    assert row["counted"] and row["confirmed_at"] is not None and row["counted_at"] is not None
    assert row["valid_observations"]==31 and len(row["events"])<=3
    engine.counter.mark_uncertainty("elsewhere",{"reason":"unrelated"})
    engine.apply_correction(cid,engine.round_number,"not_a_card")
    assert engine.counter.count_uncertain and "elsewhere" in engine.counter.uncertainties


def test_random_repeated_corrections_equal_one_contribution_per_physical_card(engine):
    rng=random.Random(81)
    cards={}
    ranks=["2","3","5","6","8","9","10","Jack","Ace"]
    from lib.logic.cards import hilo_delta
    for i in range(6):
        result,cid=engine.add_manual_card(engine.correction_context(),[30+i*32,180,50+i*32,210],ranks[i])
        assert result.ok;cards[cid]=ranks[i]
    for _ in range(80):
        cid=rng.choice(list(cards));rank=rng.choice(ranks)
        engine.apply_correction(cid,engine.round_number,"set_rank",new_rank=rank)
        cards[cid]=rank
        assert engine.counter.cards_seen==len(cards)
        assert engine.counter.running_count==sum(hilo_delta(rank) for rank in cards.values())


def test_corrections_and_archive_persist_to_real_sqlite_without_recount(engine, tmp_path):
    from lib.logic.session_store import SessionStore
    engine.store = SessionStore(tmp_path / "history.db")
    cid = observe(engine, "6")
    context = deepcopy(engine.card_registry.cards[cid].context)
    engine.new_round()
    result = engine.apply_historical_correction(cid, context, "set_rank", new_rank="10")
    assert result.ok
    engine._io_pool.submit(lambda: None).result(timeout=5)
    saved = engine.store.load_recent_shoe_state()
    assert saved["state"]["running_count"] == -1
    assert saved["state"]["cards_seen"] == 1
    archived = next(row for row in saved["state"]["card_memory"]["archive"] if row["id"] == cid)
    assert archived["rank"] == "10" and archived["manual"]
    engine.apply_shoe_state(saved)
    assert engine.counter.running_count == -1 and engine.counter.cards_seen == 1
    assert engine.card_registry.archive[cid].context == context


def test_late_suit_observation_updates_shoe_composition_once(engine):
    cid = observe(engine, "6")
    for now in (10., 11., 12.):
        engine.card_registry.observe([{"name": "6", "suit": "H", "owner": "mine",
            "cx": 80., "cy": 200., "width": 20., "height": 30., "confidence": .96}], now)
    card = engine.card_registry.cards[cid]
    assert card.suit == "H" and card.counted_name == "6 of Hearts"
    assert engine.counter.running_count == 1 and engine.counter.cards_seen == 1
    assert engine.counter.rank_seen_nosuit["6"] == 0
    assert engine.counter.suit_seen["6 of Hearts"] == 1


def test_manual_unknown_suit_cannot_be_overwritten_by_later_model(engine):
    cid = observe(engine, "6")
    assert engine.apply_correction(cid, engine.round_number, "set_rank", new_rank="10").ok
    for now in (10., 11., 12.):
        engine.card_registry.observe([{"name": "6", "suit": "H", "owner": "mine",
            "cx": 80., "cy": 200., "width": 20., "height": 30., "confidence": .99}], now)
    card = engine.card_registry.cards[cid]
    assert card.rank == "10" and card.suit is None and card.counted_name == "10"
    assert engine.counter.running_count == -1 and engine.counter.cards_seen == 1
    assert engine.counter.rank_seen_nosuit["10"] == 1


def test_displaced_observation_near_manually_added_card_remains_uncertain(engine):
    result, cid = engine.add_manual_card(engine.correction_context(), [70, 185, 90, 215], "10")
    assert result.ok
    engine.card_registry.observe([{"name": "6", "owner": "mine", "cx": 90., "cy": 200.,
        "width": 20., "height": 30., "confidence": .99}], 10.)
    assert engine.counter.cards_seen == 1 and engine.counter.running_count == -1
    assert engine.counter.count_uncertain
    pending = [c for c in engine.card_registry.cards.values() if c.id != cid]
    assert len(pending) == 1 and pending[0].possible_ids == [cid] and not pending[0].counted
