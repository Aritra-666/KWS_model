import onnxruntime as ort
import numpy as np

sess = ort.InferenceSession("repr_model_single.onnx",providers=["CPUExecutionProvider"])


def l2_normalize(x, axis=-1, eps=1e-8):
    norm = np.linalg.norm(x, axis=axis, keepdims=True)
    return x / np.clip(norm, eps, None)


class NCMClassifier:
  def __init__(self,preprocess_fn,embed_fn,embed_dim=276):
    self.preprocess_fn = preprocess_fn
    self.embed_fn = embed_fn
    self.embed_dim = embed_dim
    self.class_means = {}
    self.class_thresholds = {}
    self.global_threshold = None

  def get_embedding(self,audio):
    feats = self.preprocess_fn.extract_features(audio)
    outputs = self.embed_fn.run(None,{"input":feats.numpy().reshape(1, 1, feats.shape[0], feats.shape[1])})
    embeds = outputs[0]
    return embeds

  def enroll_keyword(self,keyword,embeddings):
    embs = np.concatenate(
            [np.asarray(e).reshape(1, self.embed_dim) for e in embeddings], axis=0
        )
    mean = l2_normalize(embs.mean(axis=0, keepdims=True), axis=1)[0]
    self.class_means[keyword] = mean

    dists = self._distance(embs, mean[None, :])
    self.class_thresholds[keyword] = float(dists.mean() + 2 * dists.std())

  def _distance(self, a, b):
     return 1 - np.sum(a * b, axis=1)  # lower = closer


  def calibrate_global_threshold(self, val_known_embs, val_unknown_embs=None, target_far=0.05):

        keywords = list(self.class_means.keys())
        means = np.stack([self.class_means[k] for k in keywords])

        def best_dist(embs):
            embs = np.asarray(embs)
            if self.metric == "cosine":
                return 1 - (embs @ means.T).max(axis=1)
            d = np.linalg.norm(embs[:, None, :] - means[None, :, :], axis=2)
            return d.min(axis=1)

        if val_unknown_embs is not None and len(val_unknown_embs) > 0:
            unk_d = best_dist(val_unknown_embs)
            thr = float(np.quantile(unk_d, target_far))
        else:
            known_d = best_dist(val_known_embs)
            thr = float(known_d.mean() + 2 * known_d.std())  # weaker fallback

        self.global_threshold = thr
        return thr

  def classify_embedding(self, embedding, use_ratio_test=True, ratio_margin=0.05):
        emb = np.asarray(embedding).reshape(1, self.embed_dim)
        keywords = list(self.class_means.keys())
        means = np.stack([self.class_means[k] for k in keywords])

        d = self._distance(np.repeat(emb, len(keywords), axis=0), means)
        order = np.argsort(d)
        best_idx = order[0]
        best_kw, best_d = keywords[best_idx], d[best_idx]
        distances = {kw: float(dd) for kw, dd in zip(keywords, d)}

        if self.global_threshold is not None and best_d > self.global_threshold:
            return None, float(best_d), distances

        if use_ratio_test and len(keywords) > 1:
            second_d = d[order[1]]
            if second_d - best_d < ratio_margin:
                return None, float(best_d), distances

        return best_kw, float(best_d), distances

  def classify(self, audio_input, use_ratio_test=True, ratio_margin=0.05):
        emb = self.get_embedding(audio_input)
        return self.classify_embedding(emb, use_ratio_test, ratio_margin)

