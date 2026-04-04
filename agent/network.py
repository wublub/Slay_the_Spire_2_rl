"""自定义网络架构：支持 action mask 的特征提取器。"""
from __future__ import annotations
import gymnasium as gym
import numpy as np
import torch as th
import torch.nn as nn
from stable_baselines3.common.torch_layers import BaseFeaturesExtractor


class StsFeatureExtractor(BaseFeaturesExtractor):
    """MLP 特征提取器，适配 STS 观测空间。"""

    def __init__(self, observation_space: gym.spaces.Box, features_dim: int = 256):
        super().__init__(observation_space, features_dim)
        input_dim = int(np.prod(observation_space.shape))
        self.net = nn.Sequential(
            nn.Linear(input_dim, 512),
            nn.ReLU(),
            nn.Linear(512, 256),
            nn.ReLU(),
            nn.Linear(256, features_dim),
            nn.ReLU(),
        )

    def forward(self, observations: th.Tensor) -> th.Tensor:
        return self.net(observations)
