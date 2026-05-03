"""
Tests for the MLP Generating Function.
Run: pytest test_mlp.py -v
"""

import sys
import os
import pytest
import torch

sys.path.insert(0, os.path.abspath('src'))
from models.mlp_generator import GeneratingFunctionMLP, load_model


class TestMLPArchitecture:
    """Test MLP model architecture and forward pass."""

    def test_output_shape(self):
        """Output shape matches output_size."""
        model = GeneratingFunctionMLP(input_size=10, output_size=5)
        x = torch.randn(1, 10)
        output = model(x)
        assert output.shape == (1, 5)

    def test_batch_forward(self):
        """Model handles batch inputs."""
        model = GeneratingFunctionMLP(input_size=4, output_size=4)
        x = torch.randn(8, 4)  # batch of 8
        output = model(x)
        assert output.shape == (8, 4)

    def test_different_sizes(self):
        """Model works with various input/output sizes."""
        for in_size, out_size in [(3, 2), (10, 10), (20, 5), (4, 8)]:
            model = GeneratingFunctionMLP(input_size=in_size, output_size=out_size)
            x = torch.randn(1, in_size)
            output = model(x)
            assert output.shape == (1, out_size)

    def test_deterministic_eval(self):
        """Model produces same output in eval mode for same input."""
        model = GeneratingFunctionMLP(input_size=4, output_size=4)
        model.eval()
        x = torch.randn(1, 4)
        out1 = model(x)
        out2 = model(x)
        assert torch.allclose(out1, out2)


class TestLoadModel:
    """Test the model loading utility."""

    def test_load_model_returns_eval_mode(self):
        model = load_model(input_size=5, output_size=3)
        assert not model.training  # should be in eval mode

    def test_load_model_forward(self):
        model = load_model(input_size=5, output_size=3)
        x = torch.randn(1, 5)
        output = model(x)
        assert output.shape == (1, 3)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
