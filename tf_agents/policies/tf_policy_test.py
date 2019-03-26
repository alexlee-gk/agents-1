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

"""Tests for tf_policy."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from absl.testing import parameterized
import numpy as np
import tensorflow as tf
from tf_agents.environments import time_step as ts
from tf_agents.policies import policy_step
from tf_agents.policies import tf_policy
from tf_agents.specs import tensor_spec
from tf_agents.utils import common as common


class TfPolicyHoldsVariables(tf_policy.Base):
  """Test tf_policy which contains only trainable variables."""

  def __init__(self, init_var_value, var_scope, name=None):
    """Initializes policy containing variables with specified value.

    Args:
      init_var_value: A scalar specifies the initial value of all variables.
      var_scope: A String defines variable scope.
      name: The name of this policy. All variables in this module will fall
        under that name. Defaults to the class name.
    """
    tf.Module.__init__(self, name=name)
    with tf.compat.v1.variable_scope(var_scope):
      self._variables_list = [
          common.create_variable("var_1", init_var_value, [3, 3],
                                 dtype=tf.float32),
          common.create_variable("var_2", init_var_value, [5, 5],
                                 dtype=tf.float32)
      ]

  def _variables(self):
    return self._variables_list

  def _action(self, time_step, policy_state, seed):
    pass

  def _distribution(self, time_step, policy_state):
    pass


class TFPolicyMismatchedDtypes(tf_policy.Base):
  """Dummy tf_policy with mismatched dtypes."""

  def __init__(self):
    observation_spec = tensor_spec.TensorSpec([2, 2], tf.float32)
    time_step_spec = ts.time_step_spec(observation_spec)
    action_spec = tensor_spec.BoundedTensorSpec([1], tf.int32, 0, 1)
    super(TFPolicyMismatchedDtypes, self).__init__(time_step_spec, action_spec)

  def _action(self, time_step, policy_state, seed):
    # This action's dtype intentionally doesn't match action_spec's dtype.
    return policy_step.PolicyStep(action=tf.constant([0], dtype=tf.int64))


class TFPolicyMismatchedDtypesListAction(tf_policy.Base):
  """Dummy tf_policy with mismatched dtypes and a list action_spec."""

  def __init__(self):
    observation_spec = tensor_spec.TensorSpec([2, 2], tf.float32)
    time_step_spec = ts.time_step_spec(observation_spec)
    action_spec = [
        tensor_spec.BoundedTensorSpec([1], tf.int64, 0, 1),
        tensor_spec.BoundedTensorSpec([1], tf.int32, 0, 1)
    ]
    super(TFPolicyMismatchedDtypesListAction, self).__init__(
        time_step_spec, action_spec)

  def _action(self, time_step, policy_state, seed):
    # This time, the action is a list where only the second dtype doesn't match.
    return policy_step.PolicyStep(action=[
        tf.constant([0], dtype=tf.int64),
        tf.constant([0], dtype=tf.int64)
    ])


class TfPolicyTest(tf.test.TestCase, parameterized.TestCase):

  @parameterized.named_parameters(
      ("SoftUpdate", 0.5, False),
      ("SyncVariables", 1.0, True),
  )
  def testUpdate(self, tau, sort_variables_by_name):
    source_policy = TfPolicyHoldsVariables(init_var_value=1.,
                                           var_scope="source")
    target_policy = TfPolicyHoldsVariables(init_var_value=0.,
                                           var_scope="target")

    self.evaluate(tf.compat.v1.global_variables_initializer())
    for var in self.evaluate(target_policy.variables()):
      self.assertAllEqual(var, np.zeros(var.shape))

    update_op = target_policy.update(
        source_policy, tau=tau, sort_variables_by_name=sort_variables_by_name)
    self.evaluate(update_op)
    for var in self.evaluate(target_policy.variables()):
      self.assertAllEqual(var, np.ones(var.shape)*tau)

  def testMismatchedDtypes(self):
    with self.assertRaisesRegexp(TypeError, ".*dtype that doesn't match.*"):
      policy = TFPolicyMismatchedDtypes()
      observation = tf.constant([[1, 2], [3, 4]], dtype=tf.float32)
      time_step = ts.restart(observation)
      policy.action(time_step)

  def testMatchedDtypes(self):
    policy = TFPolicyMismatchedDtypes()

    # Overwrite the action_spec to match the dtype of _action.
    policy._action_spec = tensor_spec.BoundedTensorSpec([1], tf.int64, 0, 1)

    observation = tf.constant([[1, 2], [3, 4]], dtype=tf.float32)
    time_step = ts.restart(observation)
    policy.action(time_step)

  def testMismatchedDtypesListAction(self):
    with self.assertRaisesRegexp(TypeError, ".*dtype that doesn't match.*"):
      policy = TFPolicyMismatchedDtypesListAction()
      observation = tf.constant([[1, 2], [3, 4]], dtype=tf.float32)
      time_step = ts.restart(observation)
      policy.action(time_step)

  def testMatchedDtypesListAction(self):
    policy = TFPolicyMismatchedDtypesListAction()

    # Overwrite the action_spec to match the dtype of _action.
    policy._action_spec = [
        tensor_spec.BoundedTensorSpec([1], tf.int64, 0, 1),
        tensor_spec.BoundedTensorSpec([1], tf.int64, 0, 1)
    ]

    observation = tf.constant([[1, 2], [3, 4]], dtype=tf.float32)
    time_step = ts.restart(observation)
    policy.action(time_step)


if __name__ == "__main__":
  tf.test.main()
