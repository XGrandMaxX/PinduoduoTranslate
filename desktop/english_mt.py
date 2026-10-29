"""Small local EN→RU translation engine for the app's English product titles."""
import re
from pathlib import Path

MODEL=Path(__file__).resolve().parents[1]/'.validation-deps/models/argos-en-ru/translate-en_ru-1_9'


class EnglishMT:
    def __init__(self):
        import ctranslate2
        import sentencepiece
        self.tokenizer=sentencepiece.SentencePieceProcessor(model_file=str(MODEL/'sentencepiece.model'))
        self.model=ctranslate2.Translator(str(MODEL/'model'),device='cpu',compute_type='int8',
                                         inter_threads=1,intra_threads=2)

    def translate(self,texts):
        normalized=[]
        for text in texts:
            # Disambiguate a furniture caster from a fantasy spell caster only
            # when the product title explicitly describes the physical hardware.
            if re.search(r'\b(?:silent|swivel|furniture|iron[- ]cased)\b',text,re.I):
                text=re.sub(r'\bcasters\b','furniture wheels',text,flags=re.I)
                text=re.sub(r'\bcaster\b','furniture wheel',text,flags=re.I)
            text=re.sub(r'\bshort sleeve shirt\b','shirt with short sleeves',text,flags=re.I)
            normalized.append(text)
        results=self.model.translate_batch([self.tokenizer.encode(t,out_type=str) for t in normalized],
            beam_size=3,max_decoding_length=96)
        return {source:self.tokenizer.decode(result.hypotheses[0]) for source,result in zip(texts,results)}
