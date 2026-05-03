import torch
import torch.nn as nn

class GeneratingFunctionMLP(nn.Module):
    """
    4-layer MLP to generate actions based on current states.
    Architecture based on CausalPlan specs: 64 -> 256 -> 256 -> 64.
    """
    def __init__(self, input_size, output_size):
        super(GeneratingFunctionMLP, self).__init__()
        self.layers = nn.Sequential(
            nn.Linear(input_size, 64),
            nn.ReLU(),
            nn.Linear(64, 256),
            nn.ReLU(),
            nn.Linear(256, 256),
            nn.ReLU(),
            nn.Linear(256, 64),
            nn.ReLU(),
            nn.Linear(64, output_size)
        )

    def forward(self, x):
        return self.layers(x)

def load_model(input_size, output_size):
    model = GeneratingFunctionMLP(input_size, output_size)
    model.eval()
    return model
