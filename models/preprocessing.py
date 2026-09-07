import torch
import torchaudio

class MFCC:
    def __init__(self):
        self.window_size_ms = 40
        self.window_stride_ms = 20
        self.sample_rate = 16000
        self.n_mfcc = 40
        self.feature_bin_count = 10

        frame_len = self.window_size_ms / 1000
        stride = self.window_stride_ms / 1000
        win_length = int(frame_len * self.sample_rate)   # 640
        hop_length = int(stride * self.sample_rate)      # 320
        n_fft = win_length
        self.mfcc = torchaudio.transforms.MFCC(
            self.sample_rate,
            n_mfcc=self.n_mfcc,
            log_mels=False,
            melkwargs={
                'win_length': win_length,
                'hop_length': hop_length,
                'n_fft': n_fft,
                "n_mels": self.n_mfcc,
                "power": 2,
                "center": False,
                "pad_mode": "constant",
                "mel_scale": 'slaney',
                "norm": 'slaney'
            }
        )
        super(MFCC, self).__init__()

    def extract_features(self, x):
        with torch.no_grad():
            features = self.mfcc(x)
        features = torch.narrow(features, -2, 0, self.feature_bin_count)
        features = features.mT  # f x t -> t x f
        return features
