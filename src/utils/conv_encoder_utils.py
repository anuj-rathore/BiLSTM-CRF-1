"""
An encoder that conv over embeddings, as described in
https://arxiv.org/abs/1705.03122.
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import tensorflow as tf


def parse_list_or_default(params_str, number, default_val, delimitor=','):
    param_list = []
    if params_str == "":
        param_list = [default_val] * number
    else:
        param_list = [int(x) for x in params_str.strip().split(delimitor)]
    return param_list


def linear_mapping_stupid(inputs, out_dim, in_dim=None, dropout=1.0, var_scope_name="linear_mapping"):
    with tf.variable_scope(var_scope_name):
        print('name', tf.get_variable_scope().name)
        input_shape_tensor = tf.shape(inputs)  # dynamic shape, no None
        input_shape = inputs.get_shape().as_list()  # static shape. may has None
        print('input_shape', input_shape)
        assert len(input_shape) == 3
        inputs = tf.reshape(inputs, [-1, input_shape_tensor[-1]])

        linear_mapping_w = tf.get_variable("linear_mapping_w", [input_shape[-1], out_dim],
                                           initializer=tf.random_normal_initializer(mean=0, stddev=tf.sqrt(
                                               dropout * 1.0 / input_shape[-1])))
        linear_mapping_b = tf.get_variable("linear_mapping_b", [out_dim], initializer=tf.zeros_initializer())

        output = tf.matmul(inputs, linear_mapping_w) + linear_mapping_b
        print('xxxxx_params', input_shape, out_dim)
        # output = tf.reshape(output, [input_shape[0], -1, out_dim])
        output = tf.reshape(output, [input_shape_tensor[0], -1, out_dim])

    return output


def linear_mapping(inputs, out_dim, in_dim=None, dropout=1.0, var_scope_name="linear_mapping"):
    with tf.variable_scope(var_scope_name):
        input_shape = inputs.get_shape().as_list()  # static shape. may has None
        return tf.contrib.layers.fully_connected(inputs=inputs, num_outputs=out_dim, activation_fn=None,
                                                 weights_initializer=tf.random_normal_initializer(mean=0,
                                                                                                  stddev=tf.sqrt(
                                                                                                      dropout * 1.0 /
                                                                                                      input_shape[-1])),
                                                 biases_initializer=tf.zeros_initializer())


def linear_mapping_weightnorm(inputs, out_dim, in_dim=None, dropout=1.0, var_scope_name="linear_mapping"):
    with tf.variable_scope(var_scope_name):
        input_shape = inputs.get_shape().as_list()  # static shape. may has None
        input_shape_tensor = tf.shape(inputs)
        # use weight normalization (Salimans & Kingma, 2016)  w = g* v/2-norm(v)
        V = tf.get_variable('V', shape=[int(input_shape[-1]), out_dim], dtype=tf.float32,
                            initializer=tf.random_normal_initializer(mean=0, stddev=tf.sqrt(
                                dropout * 1.0 / int(input_shape[-1]))), trainable=True)
        V_norm = tf.norm(V.initialized_value(), axis=0)  # V shape is M*N,  V_norm shape is N
        g = tf.get_variable('g', dtype=tf.float32, initializer=V_norm, trainable=True)
        b = tf.get_variable('b', shape=[out_dim], dtype=tf.float32, initializer=tf.zeros_initializer(),
                            trainable=True)  # weightnorm bias is init zero

        assert len(input_shape) == 3
        inputs = tf.reshape(inputs, [-1, input_shape[-1]])
        inputs = tf.matmul(inputs, V)
        inputs = tf.reshape(inputs, [input_shape_tensor[0], -1, out_dim])
        # inputs = tf.matmul(inputs, V)    # x*v

        scaler = tf.div(g, tf.norm(V, axis=0))  # g/2-norm(v)
        inputs = tf.reshape(scaler, [1, out_dim]) * inputs + tf.reshape(b, [1, out_dim])  # x*v g/2-norm(v) + b

        return inputs


def conv1d_weightnorm(inputs, layer_idx, out_dim, kernel_size, padding="SAME", dropout=1.0,
                      var_scope_name="conv_layer"):  # padding should take attention

    with tf.variable_scope("conv_layer_" + str(layer_idx)):
        in_dim = int(inputs.get_shape()[-1])
        V = tf.get_variable('V', shape=[kernel_size, in_dim, out_dim], dtype=tf.float32,
                            initializer=tf.random_normal_initializer(mean=0, stddev=tf.sqrt(
                                4.0 * dropout / (kernel_size * in_dim))), trainable=True)
        V_norm = tf.norm(V.initialized_value(), axis=[0, 1])  # V shape is M*N*k,  V_norm shape is k
        g = tf.get_variable('g', dtype=tf.float32, initializer=V_norm, trainable=True)
        b = tf.get_variable('b', shape=[out_dim], dtype=tf.float32, initializer=tf.zeros_initializer(), trainable=True)

        # use weight normalization (Salimans & Kingma, 2016)
        W = tf.reshape(g, [1, 1, out_dim]) * tf.nn.l2_normalize(V, [0, 1])
        inputs = tf.nn.bias_add(tf.nn.conv1d(value=inputs, filters=W, stride=1, padding=padding), b)
        return inputs


def gated_linear_units(inputs):
    input_shape = inputs.get_shape().as_list()
    assert len(input_shape) == 3
    input_pass = inputs[:, :, 0:int(input_shape[2] / 2)]
    input_gate = inputs[:, :, int(input_shape[2] / 2):]
    input_gate = tf.sigmoid(input_gate)
    return tf.multiply(input_pass, input_gate)


def conv_encoder_stack(inputs, nhids_list, kwidths_list, dropout_dict, mode):
    next_layer = inputs
    for layer_idx in range(len(nhids_list)):
        nin = nhids_list[layer_idx] if layer_idx == 0 else nhids_list[layer_idx - 1]
        nout = nhids_list[layer_idx]
        if nin != nout:
            # mapping for res add
            res_inputs = linear_mapping_weightnorm(next_layer, nout, dropout=dropout_dict['src'],
                                                   var_scope_name="linear_mapping_cnn_" + str(layer_idx))
        else:
            res_inputs = next_layer
        # dropout before input to conv
        next_layer = tf.contrib.layers.dropout(
            inputs=next_layer,
            keep_prob=dropout_dict['hid'],
            is_training=mode == tf.contrib.learn.ModeKeys.TRAIN)

        next_layer = conv1d_weightnorm(inputs=next_layer, layer_idx=layer_idx, out_dim=nout * 2,
                                       kernel_size=kwidths_list[layer_idx], padding="SAME", dropout=dropout_dict['hid'],
                                       var_scope_name="conv_layer_" + str(layer_idx))
        ''' 
        next_layer = tf.contrib.layers.conv2d(
            inputs=next_layer,
            num_outputs=nout*2,
            kernel_size=kwidths_list[layer_idx],
            padding="SAME",   #should take attention
            weights_initializer=tf.random_normal_initializer(mean=0, stddev=tf.sqrt(4 * dropout_dict['hid'] / (kwidths_list[layer_idx] * next_layer.get_shape().as_list()[-1]))),
            biases_initializer=tf.zeros_initializer(),
            activation_fn=None,
            scope="conv_layer_"+str(layer_idx))
        '''
        next_layer = gated_linear_units(next_layer)
        next_layer = (next_layer + res_inputs) * tf.sqrt(0.5)

    return next_layer

