# Copyright 2019, The TensorFlow Federated Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import collections

from absl.testing import absltest
from absl.testing import parameterized
import numpy as np
import tensorflow as tf

from tensorflow_federated.python.common_libs import structure
from tensorflow_federated.python.core.api import computations
from tensorflow_federated.python.core.impl.context_stack import context_stack_impl
from tensorflow_federated.python.core.impl.execution_contexts import sync_execution_context
from tensorflow_federated.python.core.impl.executors import executor_stacks
from tensorflow_federated.python.core.impl.executors import executors_errors
from tensorflow_federated.python.core.impl.federated_context import intrinsics
from tensorflow_federated.python.core.impl.types import computation_types


def _install_executor_in_synchronous_context(executor_factory_instance):
  context = sync_execution_context.ExecutionContext(executor_factory_instance)
  return context_stack_impl.context_stack.install(context)


class RetryableErrorTest(absltest.TestCase):

  def test_is_retryable_error(self):
    retryable_error = executors_errors.RetryableError()
    self.assertTrue(sync_execution_context._is_retryable_error(retryable_error))
    self.assertFalse(sync_execution_context._is_retryable_error(TypeError()))
    self.assertFalse(sync_execution_context._is_retryable_error(1))
    self.assertFalse(sync_execution_context._is_retryable_error('a'))
    self.assertFalse(sync_execution_context._is_retryable_error(None))


class ExecutionContextIntegrationTest(parameterized.TestCase):

  def test_simple_no_arg_tf_computation_with_int_result(self):

    @computations.tf_computation
    def comp():
      return tf.constant(10)

    executor = executor_stacks.local_executor_factory()
    with _install_executor_in_synchronous_context(executor):
      result = comp()

    self.assertEqual(result, 10)

  def test_one_arg_tf_computation_with_int_param_and_result(self):

    @computations.tf_computation(tf.int32)
    def comp(x):
      return tf.add(x, 10)

    executor = executor_stacks.local_executor_factory()
    with _install_executor_in_synchronous_context(executor):
      result = comp(3)

    self.assertEqual(result, 13)

  def test_three_arg_tf_computation_with_int_params_and_result(self):

    @computations.tf_computation(tf.int32, tf.int32, tf.int32)
    def comp(x, y, z):
      return tf.multiply(tf.add(x, y), z)

    executor = executor_stacks.local_executor_factory()
    with _install_executor_in_synchronous_context(executor):
      result = comp(3, 4, 5)

    self.assertEqual(result, 35)

  def test_tf_computation_with_dataset_params_and_int_result(self):

    @computations.tf_computation(computation_types.SequenceType(tf.int32))
    def comp(ds):
      return ds.reduce(np.int32(0), lambda x, y: x + y)

    executor = executor_stacks.local_executor_factory()
    with _install_executor_in_synchronous_context(executor):
      ds = tf.data.Dataset.range(10).map(lambda x: tf.cast(x, tf.int32))
      result = comp(ds)

    self.assertEqual(result, 45)

  def test_tf_computation_with_structured_result(self):

    @computations.tf_computation
    def comp():
      return collections.OrderedDict([
          ('a', tf.constant(10)),
          ('b', tf.constant(20)),
      ])

    executor = executor_stacks.local_executor_factory()
    with _install_executor_in_synchronous_context(executor):
      result = comp()

    self.assertIsInstance(result, collections.OrderedDict)
    self.assertDictEqual(result, {'a': 10, 'b': 20})

  @parameterized.named_parameters(
      ('local_executor_none_clients', executor_stacks.local_executor_factory()),
      ('local_executor_three_clients',
       executor_stacks.local_executor_factory(default_num_clients=3)),
  )
  def test_with_temperature_sensor_example(self, executor):

    @computations.tf_computation(
        computation_types.SequenceType(tf.float32), tf.float32)
    def count_over(ds, t):
      return ds.reduce(
          np.float32(0), lambda n, x: n + tf.cast(tf.greater(x, t), tf.float32))

    @computations.tf_computation(computation_types.SequenceType(tf.float32))
    def count_total(ds):
      return ds.reduce(np.float32(0.0), lambda n, _: n + 1.0)

    @computations.federated_computation(
        computation_types.at_clients(
            computation_types.SequenceType(tf.float32)),
        computation_types.at_server(tf.float32))
    def comp(temperatures, threshold):
      return intrinsics.federated_mean(
          intrinsics.federated_map(
              count_over,
              intrinsics.federated_zip(
                  [temperatures,
                   intrinsics.federated_broadcast(threshold)])),
          intrinsics.federated_map(count_total, temperatures))

    with _install_executor_in_synchronous_context(executor):
      to_float = lambda x: tf.cast(x, tf.float32)
      temperatures = [
          tf.data.Dataset.range(10).map(to_float),
          tf.data.Dataset.range(20).map(to_float),
          tf.data.Dataset.range(30).map(to_float),
      ]
      threshold = 15.0
      result = comp(temperatures, threshold)
      self.assertAlmostEqual(result, 8.333, places=3)

  def test_changing_cardinalities_across_calls(self):

    @computations.federated_computation(computation_types.at_clients(tf.int32))
    def comp(x):
      return x

    five_ints = list(range(5))
    ten_ints = list(range(10))

    executor = executor_stacks.local_executor_factory()
    with _install_executor_in_synchronous_context(executor):
      five = comp(five_ints)
      ten = comp(ten_ints)

    self.assertEqual(five, five_ints)
    self.assertEqual(ten, ten_ints)

  def test_conflicting_cardinalities_within_call(self):

    @computations.federated_computation([
        computation_types.at_clients(tf.int32),
        computation_types.at_clients(tf.int32),
    ])
    def comp(x):
      return x

    five_ints = list(range(5))
    ten_ints = list(range(10))

    executor = executor_stacks.local_executor_factory()
    with _install_executor_in_synchronous_context(executor):
      with self.assertRaisesRegex(ValueError, 'Conflicting cardinalities'):
        comp([five_ints, ten_ints])

  def test_tuple_argument_can_accept_unnamed_elements(self):

    @computations.tf_computation(tf.int32, tf.int32)
    def foo(x, y):
      return x + y

    executor = executor_stacks.local_executor_factory()
    with _install_executor_in_synchronous_context(executor):
      # pylint:disable=no-value-for-parameter
      result = foo(structure.Struct([(None, 2), (None, 3)]))
      # pylint:enable=no-value-for-parameter

    self.assertEqual(result, 5)


if __name__ == '__main__':
  absltest.main()
