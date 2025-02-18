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

"""Tests for tf_agents.bandits.agents.lin_ucb_agent."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from absl.testing import parameterized
import numpy as np
import tensorflow as tf
import tensorflow_probability as tfp
from tf_agents.bandits.agents import lin_ucb_agent
from tf_agents.bandits.agents import utils as bandit_utils
from tf_agents.bandits.drivers import driver_utils
from tf_agents.specs import tensor_spec
from tf_agents.trajectories import policy_step
from tf_agents.trajectories import time_step
from tensorflow.python.framework import test_util  # pylint: disable=g-direct-tensorflow-import  # TF internal

tfd = tfp.distributions


def test_cases():
  return parameterized.named_parameters(
      {
          'testcase_name': '_batch1_contextdim10_float32',
          'batch_size': 1,
          'context_dim': 10,
          'dtype': tf.float32,
      }, {
          'testcase_name': '_batch4_contextdim5_float64',
          'batch_size': 4,
          'context_dim': 5,
          'dtype': tf.float64,
      }, {
          'testcase_name': '_batch4_contextdim5_float64_decomp',
          'batch_size': 4,
          'context_dim': 5,
          'dtype': tf.float64,
          'use_eigendecomp': True,
      })


def _get_initial_and_final_steps(batch_size, context_dim):
  observation = np.array(range(batch_size * context_dim)).reshape(
      [batch_size, context_dim])
  reward = np.random.uniform(0.0, 1.0, [batch_size])
  initial_step = time_step.TimeStep(
      tf.constant(
          time_step.StepType.FIRST, dtype=tf.int32, shape=[batch_size],
          name='step_type'),
      tf.constant(0.0, dtype=tf.float32, shape=[batch_size], name='reward'),
      tf.constant(1.0, dtype=tf.float32, shape=[batch_size], name='discount'),
      tf.constant(observation, dtype=tf.float32,
                  shape=[batch_size, context_dim], name='observation'))
  final_step = time_step.TimeStep(
      tf.constant(
          time_step.StepType.LAST, dtype=tf.int32, shape=[batch_size],
          name='step_type'),
      tf.constant(reward, dtype=tf.float32, shape=[batch_size], name='reward'),
      tf.constant(1.0, dtype=tf.float32, shape=[batch_size], name='discount'),
      tf.constant(observation + 100.0, dtype=tf.float32,
                  shape=[batch_size, context_dim], name='observation'))
  return initial_step, final_step


def _get_action_step(action):
  return policy_step.PolicyStep(
      action=tf.convert_to_tensor(action))


def _get_experience(initial_step, action_step, final_step):
  single_experience = driver_utils.trajectory_for_bandit(
      initial_step, action_step, final_step)
  # Adds a 'time' dimension.
  return tf.nest.map_structure(
      lambda x: tf.expand_dims(tf.convert_to_tensor(x), 1),
      single_experience)


@test_util.run_all_in_graph_and_eager_modes
class LinearUCBAgentTest(tf.test.TestCase, parameterized.TestCase):

  def setUp(self):
    super(LinearUCBAgentTest, self).setUp()
    tf.compat.v1.enable_resource_variables()

  @test_cases()
  def testInitializeAgent(
      self, batch_size, context_dim, dtype, use_eigendecomp=False):
    num_actions = 5
    observation_spec = tensor_spec.TensorSpec([context_dim], tf.float32)
    time_step_spec = time_step.time_step_spec(observation_spec)
    action_spec = tensor_spec.BoundedTensorSpec(
        dtype=tf.int32, shape=(), minimum=0, maximum=num_actions - 1)
    agent = lin_ucb_agent.LinearUCBAgent(
        time_step_spec=time_step_spec,
        action_spec=action_spec,
        dtype=dtype)
    self.evaluate(agent.initialize())

  @test_cases()
  def testLinearUCBUpdate(
      self, batch_size, context_dim, dtype, use_eigendecomp=False):
    """Check LinearUCB agent updates for specified actions and rewards."""

    # Construct a `Trajectory` for the given action, observation, reward.
    num_actions = 5
    initial_step, final_step = _get_initial_and_final_steps(
        batch_size, context_dim)
    action = np.random.randint(num_actions, size=batch_size, dtype=np.int32)
    action_step = _get_action_step(action)
    experience = _get_experience(initial_step, action_step, final_step)

    # Construct an agent and perform the update.
    observation_spec = tensor_spec.TensorSpec([context_dim], tf.float32)
    time_step_spec = time_step.time_step_spec(observation_spec)
    action_spec = tensor_spec.BoundedTensorSpec(
        dtype=tf.int32, shape=(), minimum=0, maximum=num_actions - 1)
    agent = lin_ucb_agent.LinearUCBAgent(
        time_step_spec=time_step_spec,
        action_spec=action_spec,
        dtype=dtype)
    self.evaluate(agent.initialize())
    loss_info = agent.train(experience)
    self.evaluate(loss_info)
    final_a = self.evaluate(agent.cov_matrix)
    final_b = self.evaluate(agent.data_vector)

    # Compute the expected updated estimates.
    observations_list = tf.dynamic_partition(
        data=tf.reshape(experience.observation,
                        [batch_size, context_dim]),
        partitions=tf.convert_to_tensor(action),
        num_partitions=num_actions)
    rewards_list = tf.dynamic_partition(
        data=tf.reshape(experience.reward, [batch_size]),
        partitions=tf.convert_to_tensor(action),
        num_partitions=num_actions)
    expected_a_updated_list = []
    expected_b_updated_list = []
    for _, (observations_for_arm, rewards_for_arm) in enumerate(zip(
        observations_list, rewards_list)):
      num_samples_for_arm_current = tf.cast(
          tf.shape(rewards_for_arm)[0], tf.float32)
      num_samples_for_arm_total = num_samples_for_arm_current

      # pylint: disable=cell-var-from-loop
      def true_fn():
        a_new = tf.eye(context_dim) + tf.matmul(
            observations_for_arm, observations_for_arm, transpose_a=True)
        b_new = bandit_utils.sum_reward_weighted_observations(
            rewards_for_arm, observations_for_arm)
        return a_new, b_new
      def false_fn():
        return tf.eye(context_dim), tf.zeros([context_dim])
      a_new, b_new = tf.cond(
          tf.squeeze(num_samples_for_arm_total) > 0,
          true_fn,
          false_fn)

      expected_a_updated_list.append(self.evaluate(a_new))
      expected_b_updated_list.append(self.evaluate(b_new))

    # Check that the actual updated estimates match the expectations.
    self.assertAllClose(expected_a_updated_list, final_a)
    self.assertAllClose(expected_b_updated_list, final_b)

  @test_cases()
  def testLinearUCBUpdateWithForgetting(
      self, batch_size, context_dim, dtype, use_eigendecomp=False):
    """Check LinearUCB agent updates for specified actions and rewards."""
    gamma = 0.9

    # Construct a `Trajectory` for the given action, observation, reward.
    num_actions = 5
    initial_step, final_step = _get_initial_and_final_steps(
        batch_size, context_dim)
    action = np.random.randint(num_actions, size=batch_size, dtype=np.int32)
    action_step = _get_action_step(action)
    experience = _get_experience(initial_step, action_step, final_step)

    # Construct an agent and perform the update.
    observation_spec = tensor_spec.TensorSpec([context_dim], tf.float32)
    time_step_spec = time_step.time_step_spec(observation_spec)
    action_spec = tensor_spec.BoundedTensorSpec(
        dtype=tf.int32, shape=(), minimum=0, maximum=num_actions - 1)
    agent = lin_ucb_agent.LinearUCBAgent(
        time_step_spec=time_step_spec,
        action_spec=action_spec,
        gamma=gamma,
        dtype=dtype,
        use_eigendecomp=use_eigendecomp)
    self.evaluate(tf.compat.v1.global_variables_initializer())
    loss_info = agent.train(experience)
    self.evaluate(loss_info)
    final_a = self.evaluate(agent.cov_matrix)
    final_b = self.evaluate(agent.data_vector)
    final_eig_vals = self.evaluate(agent.eig_vals)

    # Compute the expected updated estimates.
    observations_list = tf.dynamic_partition(
        data=tf.reshape(experience.observation,
                        [batch_size, context_dim]),
        partitions=tf.convert_to_tensor(action),
        num_partitions=num_actions)
    rewards_list = tf.dynamic_partition(
        data=tf.reshape(experience.reward, [batch_size]),
        partitions=tf.convert_to_tensor(action),
        num_partitions=num_actions)
    expected_a_updated_list = []
    expected_b_updated_list = []
    expected_eigvals_updated_list = []
    for _, (observations_for_arm, rewards_for_arm) in enumerate(zip(
        observations_list, rewards_list)):
      num_samples_for_arm_current = tf.cast(
          tf.shape(rewards_for_arm)[0], tf.float32)
      num_samples_for_arm_total = num_samples_for_arm_current

      # pylint: disable=cell-var-from-loop
      def true_fn():
        a_new = gamma * tf.eye(context_dim) + tf.matmul(
            observations_for_arm, observations_for_arm, transpose_a=True)
        b_new = bandit_utils.sum_reward_weighted_observations(
            rewards_for_arm, observations_for_arm)
        eigmatrix_new = tf.constant([], dtype=dtype)
        eigvals_new = tf.constant([], dtype=dtype)
        if use_eigendecomp:
          eigvals_new, eigmatrix_new = tf.linalg.eigh(a_new)
        return a_new, b_new, eigvals_new, eigmatrix_new
      def false_fn():
        if use_eigendecomp:
          return (tf.eye(context_dim), tf.zeros([context_dim]),
                  tf.ones([context_dim]), tf.eye(context_dim))
        else:
          return (tf.eye(context_dim), tf.zeros([context_dim]),
                  tf.constant([], dtype=dtype), tf.constant([], dtype=dtype))
      a_new, b_new, eig_vals_new, _ = tf.cond(
          tf.squeeze(num_samples_for_arm_total) > 0,
          true_fn,
          false_fn)

      expected_a_updated_list.append(self.evaluate(a_new))
      expected_b_updated_list.append(self.evaluate(b_new))
      expected_eigvals_updated_list.append(self.evaluate(eig_vals_new))

    # Check that the actual updated estimates match the expectations.
    self.assertAllClose(expected_a_updated_list, final_a)
    self.assertAllClose(expected_b_updated_list, final_b)
    self.assertAllClose(
        expected_eigvals_updated_list, final_eig_vals, atol=1e-4, rtol=1e-4)

if __name__ == '__main__':
  tf.test.main()
