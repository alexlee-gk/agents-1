# coding=utf-8
# Copyright 2018 The TF-Agents Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Linear UCB Policy."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function


import tensorflow as tf
import tensorflow_probability as tfp
from tf_agents.bandits.policies import linalg
from tf_agents.policies import tf_policy
from tf_agents.trajectories import policy_step

tfd = tfp.distributions


class LinearUCBPolicy(tf_policy.Base):
  """Linear UCB Policy.

  Implements the Linear UCB Policy from the following paper:
  "A Contextual Bandit Approach to Personalized News Article Recommendation",
  Lihong Li, Wei Chu, John Langford, Robert Schapire, WWW 2010.

  """

  def __init__(self,
               action_spec,
               cov_matrix,
               data_vector,
               num_samples,
               time_step_spec=None,
               alpha=1.0,
               eig_vals=(),
               eig_matrix=(),
               tikhonov_weight=1.0,
               emit_log_probability=False,
               name=None):
    """Initializes `LinUCBPolicy`.

    The `a` and `b` arguments may be either `Tensor`s or `tf.Variable`s.
    If they are variables, then any assignements to those variables will be
    reflected in the output of the policy.

    Args:
      action_spec: `TensorSpec` containing action specification.
      cov_matrix: list of the covariance matrices A in the paper. There exists
        one A matrix per arm.
      data_vector: list of the b vectors in the paper. The b vector is a
        weighted sum of the observations, where the weight is the corresponding
        reward. Each arm has its own vector b.
      num_samples: list of number of samples per arm.
      time_step_spec: A `TimeStep` spec of the expected time_steps.
      alpha: a float value used to scale the confidence intervals.
      eig_vals: list of eigenvalues for each covariance matrix (one per arm).
      eig_matrix: list of eigenvectors for each covariance matrix (one per arm).
      tikhonov_weight: (float) tikhonov regularization term.
      emit_log_probability: Whether to emit log probabilities.
      name: The name of this policy.
    """
    if not isinstance(cov_matrix, (list, tuple)):
      raise ValueError('cov_matrix must be a list of matrices (Tensors).')
    self._cov_matrix = cov_matrix

    if not isinstance(data_vector, (list, tuple)):
      raise ValueError('data_vector must be a list of vectors (Tensors).')
    self._data_vector = data_vector

    if not isinstance(num_samples, (list, tuple)):
      raise ValueError('num_samples must be a list of vectors (Tensors).')
    self._num_samples = num_samples

    if not isinstance(eig_vals, (list, tuple)):
      raise ValueError('eig_vals must be a list of vectors (Tensors).')
    self._eig_vals = eig_vals

    if not isinstance(eig_matrix, (list, tuple)):
      raise ValueError('eig_matrix must be a list of vectors (Tensors).')
    self._eig_matrix = eig_matrix

    self._alpha = alpha
    self._use_eigendecomp = False
    if eig_matrix:
      self._use_eigendecomp = True
    self._tikhonov_weight = tikhonov_weight

    if len(cov_matrix) != len(data_vector):
      raise ValueError('The size of list cov_matrix must match the size of '
                       'list data_vector. Got {} for cov_matrix and {} '
                       'for data_vector'.format(
                           len(self._cov_matrix), len((data_vector))))
    if len(num_samples) != len(cov_matrix):
      raise ValueError('The size of num_samples must match the size of '
                       'list cov_matrix. Got {} for num_samples and {} '
                       'for cov_matrix'.format(
                           len(self._num_samples), len((cov_matrix))))
    if tf.nest.is_nested(action_spec):
      raise ValueError('Nested `action_spec` is not supported.')

    self._num_actions = action_spec.maximum + 1
    if self._num_actions != len(cov_matrix):
      raise ValueError(
          'The number of elements in `cov_matrix` ({}) must match '
          'the number of actions derived from `action_spec` ({}).'.format(
              len(cov_matrix), self._num_actions))
    self._observation_dim = tf.compat.dimension_value(
        time_step_spec.observation.shape[0])
    cov_matrix_dim = tf.compat.dimension_value(cov_matrix[0].shape[0])
    if self._observation_dim != cov_matrix_dim:
      raise ValueError('The dimension of matrix `cov_matrix` must match '
                       'observation dimension {}.'
                       'Got {} for `cov_matrix`.'.format(
                           self._observation_dim, cov_matrix_dim))
    data_vector_dim = tf.compat.dimension_value(data_vector[0].shape[0])
    if self._observation_dim != data_vector_dim:
      raise ValueError('The dimension of vector `data_vector` must match '
                       'observation  dimension {}. '
                       'Got {} for `data_vector`.'.format(
                           self._observation_dim, data_vector_dim))
    super(LinearUCBPolicy, self).__init__(
        time_step_spec=time_step_spec,
        action_spec=action_spec,
        emit_log_probability=emit_log_probability,
        name=name)

  def _variables(self):
    all_vars = (self._cov_matrix + self._data_vector + self._num_samples +
                list(self._eig_matrix) + list(self._eig_vals))
    return [v for v in all_vars if isinstance(v, tf.Variable)]

  def _distribution(self, time_step, policy_state):
    observation = time_step.observation
    # Check the shape of the observation matrix. The observations can be
    # batched.
    if not observation.shape.is_compatible_with([None, self._observation_dim]):
      raise ValueError('Observation shape is expected to be {}. Got {}.'.format(
          [None, self._observation_dim], observation.shape.as_list()))

    observation = tf.cast(observation, dtype=self._data_vector[0].dtype)

    p_values = []
    for k in range(self._num_actions):
      if self._use_eigendecomp:
        q_t_b = tf.matmul(
            self._eig_matrix[k],
            tf.linalg.matrix_transpose(observation),
            transpose_a=True)
        lambda_inv = tf.divide(
            tf.ones_like(self._eig_vals[k]),
            self._eig_vals[k] + self._tikhonov_weight)
        a_inv_x = tf.matmul(
            self._eig_matrix[k], tf.einsum('j,jk->jk', lambda_inv, q_t_b))
      else:
        a_inv_x = linalg.conjugate_gradient_solve(
            self._cov_matrix[k] +
            self._tikhonov_weight * tf.eye(self._observation_dim),
            tf.linalg.matrix_transpose(observation))
      est_mean_reward = tf.einsum('j,jk->k', self._data_vector[k], a_inv_x)

      ci = tf.reshape(
          tf.linalg.tensor_diag_part(tf.matmul(observation, a_inv_x)),
          [-1, 1])
      p_values.append(
          tf.reshape(est_mean_reward, [-1, 1]) + self._alpha * tf.sqrt(ci))

    # Keeping the batch dimension during the squeeze, even if batch_size == 1.
    chosen_actions = tf.argmax(
        tf.squeeze(tf.stack(p_values, axis=-1), axis=[1]),
        axis=-1,
        output_type=self._action_spec.dtype)
    action_distributions = tfp.distributions.Deterministic(loc=chosen_actions)

    return policy_step.PolicyStep(action_distributions, policy_state)
