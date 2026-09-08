"""Lightweight language detection for EN · FR · ES · JA (WS2).

Deterministic, no model: Japanese by kana/CJK code ranges; French/Spanish by
stopword + diacritic signals; English default. The answer is tagged with the
detected language and cited to the English source of truth (roadmap: "Answer
in the asker's language, cited to the English page"). When a real translation
model is configured it renders the answer in-language; offline it returns the
grounded English text with the detected-language tag, honestly.
"""
from __future__ import annotations

import os

import re

SUPPORTED = ("en", "fr", "es", "ja")
_FR = {"le", "la", "les", "que", "quoi", "pourquoi", "comment", "quel", "quelle", "est", "des", "une", "doit"}
_ES = {"el", "la", "los", "las", "que", "qué", "por", "cómo", "cuál", "cuáles", "es", "una", "debe", "para"}
_KANA_CJK = re.compile(r"[぀-ヿ一-鿿]")
_WORD = re.compile(r"[a-zà-ÿ]+", re.I)


def detect(text: str) -> str:
    if _KANA_CJK.search(text):
        return "ja"
    words = [w.lower() for w in _WORD.findall(text)]
    if not words:
        return "en"
    fr = sum(1 for w in words if w in _FR)
    es = sum(1 for w in words if w in _ES)
    # diacritic tie-breakers
    if re.search(r"[ñ¿¡]", text):
        es += 2
    if re.search(r"[çœàèù]", text):
        fr += 1
    if es > fr and es >= 1:
        return "es"
    if fr > es and fr >= 1:
        return "fr"
    return "en"


def label(code: str) -> str:
    return {"en": "English", "fr": "Français", "es": "Español", "ja": "日本語"}.get(code, code)


# Cross-lingual query normalisation. Retrieval runs on the English source of
# truth, so a non-English query is translated to English first. Offline this
# uses a compact domain lexicon (deterministic, testable); when a translation
# model is configured it is used instead. Answers are then rendered in the
# asker's language and cited to the English page (roadmap WS2).
_LEX = {
    "fr": {"critère": "criteria", "critere": "criteria", "acceptation": "acceptance",
           "couverture": "coverage", "exigence": "requirement", "livraison": "release",
           "défaut": "defect", "defaut": "defect", "traçabilité": "traceability",
           "tracabilite": "traceability", "avant": "before", "promotion": "promotion",
           "embarquement": "boarding", "procédure": "procedure", "procedure": "procedure",
           "quel": "what", "quelle": "what", "est": "is", "pour": "for", "la": "", "le": "",
           "du": "", "des": "", "un": "", "une": "", "de": "", "d": ""},
    "es": {"criterio": "criteria", "aceptación": "acceptance", "aceptacion": "acceptance",
           "cobertura": "coverage", "requisito": "requirement", "versión": "release",
           "version": "release", "defecto": "defect", "trazabilidad": "traceability",
           "antes": "before", "promoción": "promotion", "promocion": "promotion",
           "embarque": "boarding", "procedimiento": "procedure", "cuál": "what",
           "cual": "what", "qué": "what", "que": "what", "es": "is", "para": "for",
           "la": "", "el": "", "de": "", "los": "", "las": "", "un": "", "una": ""},
    "ja": {"要件": "requirement", "カバレッジ": "coverage", "受け入れ": "acceptance",
           "基準": "criteria", "リリース": "release", "欠陥": "defect", "トレーサビリティ": "traceability",
           "手順": "procedure", "搭乗": "boarding"},
}


def translate_query_to_en(question: str, code: str, model_client=None, tenant: str = "") -> str:
    if code == "en":
        return question
    # A real translation model plugs in here (opt-in via KF_TRANSLATE_MODEL=1);
    # the offline mock echoes input, so the deterministic lexicon is the default.
    if (os.environ.get("KF_TRANSLATE_MODEL") == "1" and model_client is not None
            and getattr(model_client, "available", lambda: False)()):
        try:
            out = model_client.complete(tenant, "fast", [
                {"role": "system", "content": "Translate the user text to English. Output only the translation."},
                {"role": "user", "content": question}], {"temperature": 0.0})
            if out.get("text", "").strip():
                return out["text"].strip()
        except Exception:
            pass
    lex = _LEX.get(code, {})
    if code == "ja":
        out = question
        for k, v in lex.items():
            out = out.replace(k, f" {v} ")
        return out.strip() or question
    words = re.findall(r"[\wà-ÿ]+", question.lower())
    mapped = [lex.get(w, w) for w in words]
    return " ".join(w for w in mapped if w).strip() or question
