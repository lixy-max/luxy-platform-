# Auto-generated AWQ compatibility shim
# 修复 autoawq 引用了新版 transformers 已移除的激活函数类
try:
    import math
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
    import transformers.activations as _act

    if not hasattr(_act, "PytorchGELUTanh"):
        class PytorchGELUTanh(nn.Module):
            def forward(self, x):
                return F.gelu(x, approximate="tanh")
        _act.PytorchGELUTanh = PytorchGELUTanh

    if not hasattr(_act, "GELUActivation"):
        class GELUActivation(nn.Module):
            def forward(self, x):
                return F.gelu(x)
        _act.GELUActivation = GELUActivation

    if not hasattr(_act, "NewGELUActivation"):
        def NewGELUActivation(x):
            return 0.5 * x * (1.0 + torch.tanh(
                math.sqrt(2.0 / math.pi) * (x + 0.044715 * torch.pow(x, 3))
            ))
        _act.NewGELUActivation = NewGELUActivation

    if not hasattr(_act, "QuickGELUActivation"):
        class QuickGELUActivation(nn.Module):
            def forward(self, x):
                return x * torch.sigmoid(1.702 * x)
        _act.QuickGELUActivation = QuickGELUActivation

    if not hasattr(_act, "SiLUActivation"):
        class SiLUActivation(nn.Module):
            def forward(self, x):
                return F.silu(x)
        _act.SiLUActivation = SiLUActivation

    if not hasattr(_act, "MishActivation"):
        class MishActivation(nn.Module):
            def forward(self, x):
                return x * torch.tanh(F.softplus(x))
        _act.MishActivation = MishActivation
except Exception:
    pass
