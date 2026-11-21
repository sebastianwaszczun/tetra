"""Pobiera modele do wspólnego wolumenu /models (raz, potem są w cache).

Uruchom:  docker compose exec app python scripts/download_models.py
Opcje:    --skip clip,siglip,...   pomija wybrane modele
"""
import argparse
import os

MODELS = {
    "insightface": "buffalo_l (detekcja + ArcFace 512-d)",
    "clip": "open_clip ViT-B-32 / openai (512-d)",
    "siglip": "google/siglip-base-patch16-224 (768-d)",
    "text": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 (384-d, PL)",
    "speechbrain": "speechbrain/spkrec-ecapa-voxceleb (192-d)",
}

ap = argparse.ArgumentParser()
ap.add_argument("--skip", default="")
skip = set(filter(None, ap.parse_args().skip.split(",")))


def step(key, fn):
    if key in skip:
        print(f"-- pomijam {key}")
        return
    print(f"== {key}: {MODELS[key]}")
    try:
        fn()
        print("   OK")
    except Exception as e:  # noqa: BLE001
        print(f"   BŁĄD: {type(e).__name__}: {e}")


def insightface_dl():
    from insightface.app import FaceAnalysis
    fa = FaceAnalysis(name="buffalo_l", root=os.environ.get("INSIGHTFACE_HOME", "/models/insightface"),
                      providers=["CPUExecutionProvider"])
    fa.prepare(ctx_id=-1, det_size=(640, 640))


def clip_dl():
    import open_clip
    open_clip.create_model_and_transforms("ViT-B-32", pretrained="openai",
                                          cache_dir=os.path.join(os.environ["TORCH_HOME"], "open_clip"))


def siglip_dl():
    from transformers import AutoModel, AutoProcessor
    AutoModel.from_pretrained("google/siglip-base-patch16-224")
    AutoProcessor.from_pretrained("google/siglip-base-patch16-224")


def text_dl():
    from sentence_transformers import SentenceTransformer
    SentenceTransformer("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")


def speechbrain_dl():
    try:
        from speechbrain.inference.speaker import EncoderClassifier
    except ImportError:  # starsze wersje
        from speechbrain.pretrained import EncoderClassifier
    EncoderClassifier.from_hparams(
        source="speechbrain/spkrec-ecapa-voxceleb",
        savedir=os.path.join(os.environ.get("SPEECHBRAIN_HOME", "/models/speechbrain"), "ecapa"))


step("insightface", insightface_dl)
step("clip", clip_dl)
step("siglip", siglip_dl)
step("text", text_dl)
step("speechbrain", speechbrain_dl)

print("""
Uwagi:
 * facetorch pobiera swoje modele przy pierwszym użyciu (konfiguracja Hydra z repo facetorch).
 * MagFace nie ma pakietu pip: sklonuj https://github.com/IrvingMeng/MagFace do /workspace/src/MagFace
   i pobierz wagi (magface_epoch_00025.pth) z linku w README tego repo do /models/magface/.
""")
