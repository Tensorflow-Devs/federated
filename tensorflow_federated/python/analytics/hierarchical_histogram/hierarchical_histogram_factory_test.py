# Copyright 2021, The TensorFlow Federated Authors.
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
"""Tests for hierarchical_histogram_factory."""

import collections

from absl.testing import parameterized
import numpy as np
import tensorflow as tf
import tensorflow_privacy as tfp

from tensorflow_federated.python.aggregators import differential_privacy
from tensorflow_federated.python.analytics.hierarchical_histogram import build_tree_from_leaf
from tensorflow_federated.python.analytics.hierarchical_histogram import hierarchical_histogram_factory as hihi_factory
from tensorflow_federated.python.core.api import test_case
from tensorflow_federated.python.core.backends.test import execution_contexts
from tensorflow_federated.python.core.impl.types import computation_types
from tensorflow_federated.python.core.impl.types import type_conversions
from tensorflow_federated.python.core.templates import aggregation_process
from tensorflow_federated.python.core.templates import measured_process

_test_central_dp_query = tfp.privacy.dp_query.tree_aggregation_query.CentralTreeSumQuery(
    stddev=0.0)


class TreeAggregationFactoryComputationTest(test_case.TestCase,
                                            parameterized.TestCase):

  @parameterized.product(
      value_type=[tf.float32, tf.int32],
      value_shape=[3, 4, 7, 8],
      arity=[2, 3],
  )
  def test_central_aggregation_with_sum(self, value_type, value_shape, arity):

    value_type = computation_types.to_type((value_type, (value_shape,)))

    factory_ = hihi_factory.create_central_hierarchical_histogram_factory(
        arity=arity)
    self.assertIsInstance(factory_,
                          differential_privacy.DifferentiallyPrivateFactory)

    process = factory_.create(value_type)
    self.assertIsInstance(process, aggregation_process.AggregationProcess)

    query_state = _test_central_dp_query.initial_global_state()
    query_state_type = type_conversions.type_from_tensors(query_state)
    query_metrics_type = type_conversions.type_from_tensors(
        _test_central_dp_query.derive_metrics(query_state))

    server_state_type = computation_types.at_server((query_state_type, ()))
    expected_initialize_type = computation_types.FunctionType(
        parameter=None, result=server_state_type)
    self.assertTrue(
        process.initialize.type_signature.is_equivalent_to(
            expected_initialize_type))

    expected_measurements_type = computation_types.at_server(
        collections.OrderedDict(dp_query_metrics=query_metrics_type, dp=()))
    result_value_type = computation_types.to_type(
        collections.OrderedDict([
            ('flat_values',
             computation_types.TensorType(tf.float32, tf.TensorShape(None))),
            ('nested_row_splits', [(tf.int64, (None,))])
        ]))
    expected_next_type = computation_types.FunctionType(
        parameter=collections.OrderedDict(
            state=server_state_type,
            value=computation_types.at_clients(value_type)),
        result=measured_process.MeasuredProcessOutput(
            state=server_state_type,
            result=computation_types.at_server(result_value_type),
            measurements=expected_measurements_type))

    self.assertTrue(
        process.next.type_signature.is_equivalent_to(expected_next_type))

  @parameterized.product(
      value_shape=[4, 7],
      arity=[2, 3],
      l1_bound=[10],
  )
  def test_central_aggregation_with_secure_sum(self, value_shape, arity,
                                               l1_bound):

    value_type = computation_types.to_type((tf.float32, (value_shape,)))

    factory_ = hihi_factory.create_central_hierarchical_histogram_factory(
        arity=arity, secure_sum=True)
    self.assertIsInstance(factory_,
                          differential_privacy.DifferentiallyPrivateFactory)

    process = factory_.create(value_type)
    self.assertIsInstance(process, aggregation_process.AggregationProcess)

    query_state = _test_central_dp_query.initial_global_state()
    query_state_type = type_conversions.type_from_tensors(query_state)
    query_metrics_type = type_conversions.type_from_tensors(
        _test_central_dp_query.derive_metrics(query_state))

    server_state_type = computation_types.at_server((query_state_type, ()))
    expected_initialize_type = computation_types.FunctionType(
        parameter=None, result=server_state_type)
    self.assertTrue(
        process.initialize.type_signature.is_equivalent_to(
            expected_initialize_type))

    secure_dp_type = collections.OrderedDict(
        secure_upper_clipped_count=tf.int32,
        secure_lower_clipped_count=tf.int32,
        secure_upper_threshold=tf.float32,
        secure_lower_threshold=tf.float32)
    expected_measurements_type = computation_types.at_server(
        collections.OrderedDict(
            dp_query_metrics=query_metrics_type, dp=secure_dp_type))
    result_value_type = computation_types.to_type(
        collections.OrderedDict([
            ('flat_values',
             computation_types.TensorType(tf.float32, tf.TensorShape(None))),
            ('nested_row_splits', [(tf.int64, (None,))])
        ]))
    expected_next_type = computation_types.FunctionType(
        parameter=collections.OrderedDict(
            state=server_state_type,
            value=computation_types.at_clients(value_type)),
        result=measured_process.MeasuredProcessOutput(
            state=server_state_type,
            result=computation_types.at_server(result_value_type),
            measurements=expected_measurements_type))

    self.assertTrue(
        process.next.type_signature.is_equivalent_to(expected_next_type))


class TreeAggregationFactoryExecutionTest(test_case.TestCase,
                                          parameterized.TestCase):

  @parameterized.product(
      histogram_size=[7, 8],
      num_clients=[8, 13],
      arity=[2, 3],
      l1_bound=[10],
      secure_sum=[True, False],
  )
  def test_central_aggregation(self, histogram_size, num_clients, arity,
                               l1_bound, secure_sum):
    agg_factory = hihi_factory.create_central_hierarchical_histogram_factory(
        arity=arity,
        max_records_per_user=l1_bound,
        stddev=0.0,
        secure_sum=secure_sum)
    value_type = computation_types.to_type((tf.float32, (histogram_size,)))
    process = agg_factory.create(value_type)
    state = process.initialize()

    client_histograms = []
    for _ in range(num_clients):
      client_histograms.append(np.arange(histogram_size, dtype=float).tolist())

    output = process.next(state, client_histograms)

    clipped_client_histograms = [
        np.divide(
            np.multiply(x, l1_bound).tolist(),
            max(l1_bound, np.linalg.norm(x, ord=1))) for x in client_histograms
    ]
    summed_histogram = np.sum(clipped_client_histograms, axis=0).tolist()
    reference_hierarchical_histogram = build_tree_from_leaf.create_hierarchical_histogram(
        summed_histogram, arity)

    self.assertAllClose(reference_hierarchical_histogram, output.result)

  @parameterized.product(
      histogram_size=[7],
      num_clients=[4],
      arity=[2, 3],
      l1_bound=[10],
      stddev=[0.1, 0.5, 1.0],
      secure_sum=[True, False],
  )
  def test_central_aggregation_with_noise(self, histogram_size, num_clients,
                                          arity, l1_bound, stddev, secure_sum):
    agg_factory = hihi_factory.create_central_hierarchical_histogram_factory(
        stddev=stddev,
        arity=arity,
        max_records_per_user=l1_bound,
        secure_sum=secure_sum)
    value_type = computation_types.to_type((tf.float32, (histogram_size,)))
    process = agg_factory.create(value_type)
    state = process.initialize()

    client_histograms = []
    for _ in range(num_clients):
      client_histograms.append(np.arange(histogram_size, dtype=float).tolist())

    output = process.next(state, client_histograms)

    clipped_client_histograms = [
        np.divide(
            np.multiply(x, l1_bound).tolist(),
            max(l1_bound, np.linalg.norm(x, ord=1))) for x in client_histograms
    ]
    summed_histogram = np.sum(clipped_client_histograms, axis=0).tolist()
    reference_hierarchical_histogram = build_tree_from_leaf.create_hierarchical_histogram(
        summed_histogram, arity)

    self.assertAllClose(
        reference_hierarchical_histogram, output.result, atol=6 * stddev)

  @parameterized.named_parameters(
      ('stddev_error', -0.1, 2, 1),
      ('arity_error', 0.1, 1, 1),
      ('l1_bound', 0.1, 1, 0),
  )
  def test_central_aggregation_raise(self, stddev, arity, l1_bound):

    with self.assertRaises(ValueError):
      hihi_factory.create_central_hierarchical_histogram_factory(
          stddev=stddev, arity=arity, max_records_per_user=l1_bound)


if __name__ == '__main__':
  execution_contexts.set_test_execution_context()
  test_case.main()
