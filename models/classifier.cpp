
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <cmath>
#include <fstream>
#include <iostream>
#include <string>
#include <vector>
#include <algorithm>
#include <limits>

namespace cfg {
constexpr float kSampleRate      = 16000.0;
constexpr float kDurationSec     = 1.0;
constexpr int    kNumSamples      = static_cast<int>(kSampleRate * kDurationSec);

constexpr int    kWinLength       = 640;
constexpr int    kHopLength       = 320;
constexpr int    kNFft            = 640;

constexpr int    kNMels           = 40;
constexpr int    kNMfcc           = 40;
constexpr int    kFeatureBinCount = 10;

constexpr float kFMin            = 0.0;
constexpr float kFMax            = kSampleRate / 2.0;

constexpr float kTopDb           = 80.0;
constexpr float kAmin            = 1e-10;
}
static float HzToMelSlaney(float f) {
    constexpr float f_min = 0.0;
    constexpr float f_sp = 200.0 / 3.0;
    constexpr float min_log_hz = 1000.0;
    constexpr float min_log_mel = (min_log_hz - f_min) / f_sp;
    constexpr float logstep = 0.06875177742094912;
    if (f < min_log_hz) {
        return (f - f_min) / f_sp;
    }
    return min_log_mel + std::log(f / min_log_hz) / logstep;
}

static float MelToHzSlaney(float mel) {
    constexpr float f_min = 0.0;
    constexpr float f_sp = 200.0 / 3.0;
    constexpr float min_log_hz = 1000.0;
    constexpr float min_log_mel = (min_log_hz - f_min) / f_sp;
    constexpr float logstep = 0.06875177742094912;

    if (mel < min_log_mel) {
        return f_min + f_sp * mel;
    }
    return min_log_hz * std::exp(logstep * (mel - min_log_mel));
}


static std::vector<float> LoadWav16(const std::string& path, int expected_sample_rate) {
    std::ifstream f(path, std::ios::binary);
    if (!f) return {};
    char riff[4]; f.read(riff, 4);
    if (std::strncmp(riff, "RIFF", 4) != 0) return {};
    f.seekg(4, std::ios::cur);
    char wave[4]; f.read(wave, 4);
    if (std::strncmp(wave, "WAVE", 4) != 0) return {};

    uint16_t num_channels = 1, bits_per_sample = 16;
    uint32_t sample_rate = 0;
    std::vector<float> samples;

    while (f) {
        char chunk_id[4];
        f.read(chunk_id, 4);
        if (!f) break;
        uint32_t chunk_size = 0;
        f.read(reinterpret_cast<char*>(&chunk_size), 4);
        if (!f) break;

        if (std::strncmp(chunk_id, "fmt ", 4) == 0) {
            uint16_t audio_format = 0;
            f.read(reinterpret_cast<char*>(&audio_format), 2);
            f.read(reinterpret_cast<char*>(&num_channels), 2);
            f.read(reinterpret_cast<char*>(&sample_rate), 4);
            f.seekg(6, std::ios::cur);
            f.read(reinterpret_cast<char*>(&bits_per_sample), 2);
            long consumed = 2 + 2 + 4 + 4 + 2 + 2;
            if (static_cast<long>(chunk_size) > consumed) f.seekg(chunk_size - consumed, std::ios::cur);
        } else if (std::strncmp(chunk_id, "data", 4) == 0) {
            size_t num_values = chunk_size / (bits_per_sample / 8);
            std::vector<int16_t> raw(num_values);
            f.read(reinterpret_cast<char*>(raw.data()), chunk_size);
            size_t frames = num_values / num_channels;
            samples.resize(frames);
            for (size_t i = 0; i < frames; ++i) {
                float acc = 0.0;
                for (int c = 0; c < num_channels; ++c) acc += raw[i * num_channels + c] / 32768.0;
                samples[i] = acc / num_channels;
            }
        } else {
            f.seekg(chunk_size, std::ios::cur);
        }
    }
    if (samples.empty()) return {};
    if (sample_rate != 0 && static_cast<int>(sample_rate) != expected_sample_rate) {
        std::cerr << "Warning: WAV sample rate (" << sample_rate << " Hz) != expected "
                  << expected_sample_rate << " Hz. No resampling performed.\n";
    }
    return samples;
}



class TorchaudioStyleMfcc {
public:
    TorchaudioStyleMfcc() {
        BuildPeriodicHann();
        BuildSlaneyMelFilterbank();
    }


    std::vector<float> PowerSpectrum(const std::vector<float>& windowed_frame) const {
        const int N = cfg::kNFft;
        const int num_bins = N / 2 + 1;
        std::vector<float> power(num_bins);
        for (int k = 0; k < num_bins; ++k) {
            float re = 0.0, im = 0.0;
            float ang_step = -2.0 * M_PI * k / N;
            for (int n = 0; n < N; ++n) {
                float ang = ang_step * n;
                re += windowed_frame[n] * std::cos(ang);
                im += windowed_frame[n] * std::sin(ang);
            }
            power[k] = re * re + im * im;
        }
        return power;
    }

    std::vector<float> MelEnergies(const std::vector<float>& power) const {
        std::vector<float> mel(cfg::kNMels, 0.0);
        for (int m = 0; m < cfg::kNMels; ++m) {
            float sum = 0.0;
            for (size_t k = 0; k < power.size(); ++k) sum += power[k] * filterbank_[m][k];
            mel[m] = sum;
        }
        return mel;
    }

    std::vector<float> Dct(const std::vector<float>& mel_db, int num_out) const {
        std::vector<float> out(num_out, 0.0);
        const int N = static_cast<int>(mel_db.size());
        for (int k = 0; k < num_out; ++k) {
            float sum = 0.0;
            for (int n = 0; n < N; ++n) {
                sum += mel_db[n] * std::cos(M_PI / N * (n + 0.5) * k);
            }
            float scale = (k == 0) ? std::sqrt(1.0 / N) : std::sqrt(2.0 / N);
            out[k] = sum * scale;
        }
        return out;
    }

    const std::vector<float>& hann() const { return hann_; }

private:
    void BuildPeriodicHann() {
        hann_.resize(cfg::kWinLength);
        const int N = cfg::kWinLength;
        for (int n = 0; n < N; ++n) {
            hann_[n] = 0.5 - 0.5 * std::cos(2.0 * M_PI * n / N);
        }
    }

    void BuildSlaneyMelFilterbank() {
        const int n_freqs = cfg::kNFft / 2 + 1;
        std::vector<float> freqs(n_freqs);
        for (int k = 0; k < n_freqs; ++k) {
            freqs[k] = k * cfg::kSampleRate / cfg::kNFft;
        }

        float m_min = HzToMelSlaney(cfg::kFMin);
        float m_max = HzToMelSlaney(cfg::kFMax);
        std::vector<float> mel_pts(cfg::kNMels + 2);
        for (int i = 0; i < cfg::kNMels + 2; ++i) {
            mel_pts[i] = m_min + (m_max - m_min) * i / (cfg::kNMels + 1);
        }
        std::vector<float> hz_pts(cfg::kNMels + 2);
        for (int i = 0; i < cfg::kNMels + 2; ++i) hz_pts[i] = MelToHzSlaney(mel_pts[i]);

        filterbank_.assign(cfg::kNMels, std::vector<float>(n_freqs, 0.0));
        for (int m = 0; m < cfg::kNMels; ++m) {
            float f_left   = hz_pts[m];
            float f_center = hz_pts[m + 1];
            float f_right  = hz_pts[m + 2];
            float enorm = 2.0 / (f_right - f_left);

            for (int k = 0; k < n_freqs; ++k) {
                float freq = freqs[k];
                float down_slope = (freq - f_left) / (f_center - f_left);
                float up_slope   = (f_right - freq) / (f_right - f_center);
                float w = std::max(0.0f, std::min(down_slope, up_slope));
                filterbank_[m][k] = w * enorm;
            }
        }
    }

    std::vector<float> hann_;
    std::vector<std::vector<float>> filterbank_;
};


static std::vector<std::vector<float>> ComputeMfcc(std::vector<float> signal) {
    using namespace cfg;

    signal.resize(kNumSamples, 0.0);

    TorchaudioStyleMfcc extractor;

    int num_frames = 0;
    if (kNumSamples >= kWinLength) {
        num_frames = 1 + (kNumSamples - kWinLength) / kHopLength;
    }

    std::vector<std::vector<float>> mel_power(num_frames, std::vector<float>(kNMels));
    std::vector<float> frame(kWinLength);
    for (int f = 0; f < num_frames; ++f) {
        int start = f * kHopLength;
        for (int i = 0; i < kWinLength; ++i) frame[i] = signal[start + i] * extractor.hann()[i];
        std::vector<float> power = extractor.PowerSpectrum(frame);
        mel_power[f] = extractor.MelEnergies(power);
    }


    float global_max_db = -std::numeric_limits<float>::infinity();
    std::vector<std::vector<float>> mel_db(num_frames, std::vector<float>(kNMels));
    for (int f = 0; f < num_frames; ++f) {
        for (int m = 0; m < kNMels; ++m) {
            float x = std::max(mel_power[f][m], kAmin);
            float db = 10.0 * std::log10(x);
            mel_db[f][m] = db;
            global_max_db = std::max(global_max_db, db);
        }
    }
    float clamp_floor = global_max_db - kTopDb;
    for (int f = 0; f < num_frames; ++f)
        for (int m = 0; m < kNMels; ++m)
            mel_db[f][m] = std::max(mel_db[f][m], clamp_floor);

    std::vector<std::vector<float>> out(num_frames, std::vector<float>(kFeatureBinCount));
    for (int f = 0; f < num_frames; ++f) {
        std::vector<float> full_mfcc = extractor.Dct(mel_db[f], kNMfcc);
        std::copy(full_mfcc.begin(), full_mfcc.begin() + kFeatureBinCount, out[f].begin());
    }

    return out;
}


