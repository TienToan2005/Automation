"""Chuyển trọng số MiniFASNet (Silent-Face-Anti-Spoofing, .pth) sang ONNX để ai_service dùng với onnxruntime.

Chỉ cần chạy một lần (cần `pip install torch onnx onnxscript`, không cần cài trong môi trường chạy ai_service):

  git clone https://github.com/minivision-ai/Silent-Face-Anti-Spoofing
  python tools/export_pad_onnx.py --repo <thư_mục_repo_vừa_clone> --out models

Kết quả: models/2.7_80x80_MiniFASNetV2.onnx và models/4_0_0_80x80_MiniFASNetV1SE.onnx
"""
import argparse
import os
import sys
from collections import OrderedDict

import torch

MODELS = [
    ("2.7_80x80_MiniFASNetV2", "MiniFASNetV2"),
    ("4_0_0_80x80_MiniFASNetV1SE", "MiniFASNetV1SE"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="thư mục repo Silent-Face-Anti-Spoofing")
    ap.add_argument("--weights", help="thư mục chứa .pth (mặc định: <repo>/resources/anti_spoof_models)")
    ap.add_argument("--out", default="models")
    args = ap.parse_args()

    sys.path.insert(0, args.repo)
    from src.model_lib import MiniFASNet as arch_lib  # noqa: E402

    weights = args.weights or os.path.join(args.repo, "resources", "anti_spoof_models")
    os.makedirs(args.out, exist_ok=True)

    for stem, arch in MODELS:
        h, w = 80, 80
        kernel = ((h + 15) // 16, (w + 15) // 16)
        model = getattr(arch_lib, arch)(conv6_kernel=kernel, num_classes=3)
        state = torch.load(os.path.join(weights, stem + ".pth"), map_location="cpu")
        state = OrderedDict((k[7:] if k.startswith("module.") else k, v) for k, v in state.items())
        model.load_state_dict(state)
        model.eval()
        out = os.path.join(args.out, stem + ".onnx")
        torch.onnx.export(model, torch.zeros(1, 3, h, w), out, input_names=["input"],
                          output_names=["logits"], opset_version=17, dynamo=False)
        print("Exported", out)


if __name__ == "__main__":
    main()
