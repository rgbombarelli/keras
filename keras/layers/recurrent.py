# -*- coding: utf-8 -*-
from __future__ import absolute_import
import numpy as np

from .. import backend as K
from .. import activations, initializations, regularizers
from ..layers.core import MaskedLayer


def time_distributed_dense(x, w, b=None, dropout=None,
                           input_dim=None, output_dim=None, timesteps=None):
    '''Apply y.w + b for every temporal slice y of x.
    '''
    if not input_dim:
        # won't work with TensorFlow
        input_dim = K.shape(x)[2]
    if not timesteps:
        # won't work with TensorFlow
        timesteps = K.shape(x)[1]
    if not output_dim:
        # won't work with TensorFlow
        output_dim = K.shape(w)[1]

    if dropout:
        # apply the same dropout pattern at every timestep
        ones = K.ones_like(K.reshape(x[:, 0, :], (-1, input_dim)))
        dropout_matrix = K.dropout(ones, dropout)
        expanded_dropout_matrix = K.repeat(dropout_matrix, timesteps)
        x *= expanded_dropout_matrix

    # collapse time dimension and batch dimension together
    x = K.reshape(x, (-1, input_dim))

    x = K.dot(x, w)
    if b:
        x = x + b
    # reshape to 3D tensor
    x = K.reshape(x, (-1, timesteps, output_dim))
    return x


class Recurrent(MaskedLayer):
    '''Abstract base class for recurrent layers.
    Do not use in a model -- it's not a functional layer!

    All recurrent layers (GRU, LSTM, SimpleRNN) also
    follow the specifications of this class and accept
    the keyword arguments listed below.

    # Input shape
        3D tensor with shape `(nb_samples, timesteps, input_dim)`.

    # Output shape
        - if `return_sequences`: 3D tensor with shape
            `(nb_samples, timesteps, output_dim)`.
        - else, 2D tensor with shape `(nb_samples, output_dim)`.

    # Arguments
        weights: list of numpy arrays to set as initial weights.
            The list should have 3 elements, of shapes:
            `[(input_dim, output_dim), (output_dim, output_dim), (output_dim,)]`.
        return_sequences: Boolean. Whether to return the last output
            in the output sequence, or the full sequence.
        go_backwards: Boolean (default False).
            If True, process the input sequence backwards.
        stateful: Boolean (default False). If True, the last state
            for each sample at index i in a batch will be used as initial
            state for the sample of index i in the following batch.
        input_dim: dimensionality of the input (integer).
            This argument (or alternatively, the keyword argument `input_shape`)
            is required when using this layer as the first layer in a model.
        input_length: Length of input sequences, to be specified
            when it is constant.
            This argument is required if you are going to connect
            `Flatten` then `Dense` layers upstream
            (without it, the shape of the dense outputs cannot be computed).
            Note that if the recurrent layer is not the first layer
            in your model, you would need to specify the input length
            at the level of the first layer
            (e.g. via the `input_shape` argument)

    # Masking
        This layer supports masking for input data with a variable number
        of timesteps. To introduce masks to your data,
        use an [Embedding](embeddings.md) layer with the `mask_zero` parameter
        set to `True`.

    # TensorFlow warning
        For the time being, when using the TensorFlow backend,
        the number of timesteps used must be specified in your model.
        Make sure to pass an `input_length` int argument to your
        recurrent layer (if it comes first in your model),
        or to pass a complete `input_shape` argument to the first layer
        in your model otherwise.


    # Note on using statefulness in RNNs
        You can set RNN layers to be 'stateful', which means that the states
        computed for the samples in one batch will be reused as initial states
        for the samples in the next batch.
        This assumes a one-to-one mapping between
        samples in different successive batches.

        To enable statefulness:
            - specify `stateful=True` in the layer constructor.
            - specify a fixed batch size for your model, by passing
                a `batch_input_shape=(...)` to the first layer in your model.
                This is the expected shape of your inputs *including the batch size*.
                It should be a tuple of integers, e.g. `(32, 10, 100)`.

        To reset the states of your model, call `.reset_states()` on either
        a specific layer, or on your entire model.

    # Note on using dropout with TensorFlow
        When using the TensorFlow backend, specify a fixed batch size for your model
        following the notes on statefulness RNNs.
    '''
    input_ndim = 3

    def __init__(self, weights=None,
                 return_sequences=False, go_backwards=False, stateful=False,
                 input_dim=None, input_length=None, **kwargs):
        self.return_sequences = return_sequences
        self.initial_weights = weights
        self.go_backwards = go_backwards
        self.stateful = stateful

        self.input_dim = input_dim
        self.input_length = input_length
        if self.input_dim:
            kwargs['input_shape'] = (self.input_length, self.input_dim)
        super(Recurrent, self).__init__(**kwargs)

    def get_output_mask(self, train=False):
        if self.return_sequences:
            return super(Recurrent, self).get_output_mask(train)
        else:
            return None

    @property
    def output_shape(self):
        input_shape = self.input_shape
        if self.return_sequences:
            return (input_shape[0], input_shape[1], self.output_dim)
        else:
            return (input_shape[0], self.output_dim)

    def step(self, x, states):
        raise NotImplementedError

    def get_constants(self, x, train=False):
        return []

    def get_initial_states(self, x):
        # build an all-zero tensor of shape (samples, output_dim)
        initial_state = K.zeros_like(x)  # (samples, timesteps, input_dim)
        initial_state = K.sum(initial_state, axis=1)  # (samples, input_dim)
        reducer = K.zeros((self.input_dim, self.output_dim))
        initial_state = K.dot(initial_state, reducer)  # (samples, output_dim)
        initial_states = [initial_state for _ in range(len(self.states))]
        return initial_states

    def preprocess_input(self, x, train=False):
        return x

    def get_output(self, train=False):
        # input shape: (nb_samples, time (padded with zeros), input_dim)
        X = self.get_input(train)
        mask = self.get_input_mask(train)

        assert K.ndim(X) == 3
        if K._BACKEND == 'tensorflow':
            if not self.input_shape[1]:
                raise Exception('When using TensorFlow, you should define ' +
                                'explicitly the number of timesteps of ' +
                                'your sequences.\n' +
                                'If your first layer is an Embedding, ' +
                                'make sure to pass it an "input_length" ' +
                                'argument. Otherwise, make sure ' +
                                'the first layer has ' +
                                'an "input_shape" or "batch_input_shape" ' +
                                'argument, including the time axis.')
        if self.stateful:
            initial_states = self.states
        else:
            initial_states = self.get_initial_states(X)
        constants = self.get_constants(X, train)
        preprocessed_input = self.preprocess_input(X, train)

        last_output, outputs, states = K.rnn(self.step, preprocessed_input,
                                             initial_states,
                                             go_backwards=self.go_backwards,
                                             mask=mask,
                                             constants=constants)
        if self.stateful:
            self.updates = []
            for i in range(len(states)):
                self.updates.append((self.states[i], states[i]))

        if self.return_sequences:
            return outputs
        else:
            return last_output

    def get_config(self):
        config = {"name": self.__class__.__name__,
                  "return_sequences": self.return_sequences,
                  "go_backwards": self.go_backwards,
                  "stateful": self.stateful}
        if self.stateful:
            config['batch_input_shape'] = self.input_shape
        else:
            config['input_dim'] = self.input_dim
            config['input_length'] = self.input_length

        base_config = super(Recurrent, self).get_config()
        return dict(list(base_config.items()) + list(config.items()))


class SimpleRNN(Recurrent):
    '''Fully-connected RNN where the output is to be fed back to input.

    # Arguments
        output_dim: dimension of the internal projections and the final output.
        init: weight initialization function.
            Can be the name of an existing function (str),
            or a Theano function (see: [initializations](../initializations.md)).
        inner_init: initialization function of the inner cells.
        activation: activation function.
            Can be the name of an existing function (str),
            or a Theano function (see: [activations](../activations.md)).
        W_regularizer: instance of [WeightRegularizer](../regularizers.md)
            (eg. L1 or L2 regularization), applied to the input weights matrices.
        U_regularizer: instance of [WeightRegularizer](../regularizers.md)
            (eg. L1 or L2 regularization), applied to the recurrent weights matrices.
        b_regularizer: instance of [WeightRegularizer](../regularizers.md),
            applied to the bias.
        dropout_W: float between 0 and 1. Fraction of the input units to drop for input gates.
        dropout_U: float between 0 and 1. Fraction of the input units to drop for recurrent connections.

    # References
        - [A Theoretically Grounded Application of Dropout in Recurrent Neural Networks](http://arxiv.org/abs/1512.05287)
    '''
    def __init__(self, output_dim,
                 init='glorot_uniform', inner_init='orthogonal',
                 activation='tanh',
                 W_regularizer=None, U_regularizer=None, b_regularizer=None,
                 dropout_W=0., dropout_U=0., **kwargs):
        self.output_dim = output_dim
        self.init = initializations.get(init)
        self.inner_init = initializations.get(inner_init)
        self.activation = activations.get(activation)
        self.W_regularizer = regularizers.get(W_regularizer)
        self.U_regularizer = regularizers.get(U_regularizer)
        self.b_regularizer = regularizers.get(b_regularizer)
        self.dropout_W, self.dropout_U = dropout_W, dropout_U
        super(SimpleRNN, self).__init__(**kwargs)

    def build(self):
        input_shape = self.input_shape
        if self.stateful:
            self.reset_states()
        else:
            # initial states: all-zero tensor of shape (output_dim)
            self.states = [None]
        input_dim = input_shape[2]
        self.input_dim = input_dim

        self.W = self.init((input_dim, self.output_dim),
                           name='{}_W'.format(self.name))
        self.U = self.inner_init((self.output_dim, self.output_dim),
                                 name='{}_U'.format(self.name))
        self.b = K.zeros((self.output_dim,), name='{}_b'.format(self.name))

        self.regularizers = []
        if self.W_regularizer:
            self.W_regularizer.set_param(self.W)
            self.regularizers.append(self.W_regularizer)
        if self.U_regularizer:
            self.W_regularizer.set_param(self.U)
            self.regularizers.append(self.U_regularizer)
        if self.b_regularizer:
            self.W_regularizer.set_param(self.b)
            self.regularizers.append(self.b_regularizer)

        self.trainable_weights = [self.W, self.U, self.b]

        if self.initial_weights is not None:
            self.set_weights(self.initial_weights)
            del self.initial_weights

    def reset_states(self):
        assert self.stateful, 'Layer must be stateful.'
        input_shape = self.input_shape
        if not input_shape[0]:
            raise Exception('If a RNN is stateful, a complete ' +
                            'input_shape must be provided (including batch size).')
        if hasattr(self, 'states'):
            K.set_value(self.states[0],
                        np.zeros((input_shape[0], self.output_dim)))
        else:
            self.states = [K.zeros((input_shape[0], self.output_dim))]

    def preprocess_input(self, x, train=False):
        if train and (0 < self.dropout_W < 1):
            dropout = self.dropout_W
        else:
            dropout = 0
        input_shape = self.input_shape
        input_dim = input_shape[2]
        timesteps = input_shape[1]
        return time_distributed_dense(x, self.W, self.b, dropout,
                                      input_dim, self.output_dim, timesteps)

    def step(self, h, states):
        prev_output = states[0]
        if len(states) == 2:
            B_U = states[1]
        else:
            B_U = 1.
        output = self.activation(h + K.dot(prev_output * B_U, self.U))
        return output, [output]

    def get_constants(self, x, train=False):
        if train and (0 < self.dropout_U < 1):
            ones = K.ones_like(K.reshape(x[:, 0, 0], (-1, 1)))
            ones = K.concatenate([ones] * self.output_dim, 1)
            B_U = K.dropout(ones, self.dropout_U)
            return [B_U]
        return []

    def get_config(self):
        config = {"output_dim": self.output_dim,
                  "init": self.init.__name__,
                  "inner_init": self.inner_init.__name__,
                  "activation": self.activation.__name__,
                  "W_regularizer": self.W_regularizer.get_config() if self.W_regularizer else None,
                  "U_regularizer": self.U_regularizer.get_config() if self.U_regularizer else None,
                  "b_regularizer": self.b_regularizer.get_config() if self.b_regularizer else None,
                  "dropout_W": self.dropout_W,
                  "dropout_U": self.dropout_U}
        base_config = super(SimpleRNN, self).get_config()
        return dict(list(base_config.items()) + list(config.items()))


class TerminalSRNN(SimpleRNN):
    '''Fully-connected RNN where the output is to be fed back to input.

    '''
    def __init__(self, output_dim, temperature=1, **kwargs):
        super(TerminalSRNN, self).__init__(output_dim, **kwargs)
        self.temperature = temperature

    def build(self):
        super(TerminalSRNN, self).build()
        self.Y = self.inner_init((self.output_dim, self.output_dim),
                                 name='{}_Y'.format(self.name))
        self.trainable_weights += [self.Y]

    def get_initial_states(self, x):
        # build an all-zero tensor of shape (samples, output_dim)
        initial_state = K.zeros_like(x)  # (samples, timesteps, input_dim)
        initial_state = K.sum(initial_state, axis=1)  # (samples, input_dim)
        reducer = K.zeros((self.input_dim, self.output_dim))
        initial_state = K.dot(initial_state, reducer)  # (samples, output_dim)
        initial_states = [(initial_state, initial_state) for _ in range(len(self.states))]
        return initial_states

    def get_output(self, train=False):
        # input shape: (nb_samples, time (padded with zeros), input_dim)
        X = self.get_input(train)
        mask = self.get_input_mask(train)

        assert K.ndim(X) == 3

        if self.stateful:
            initial_states = self.states
        else:
            initial_states = self.get_initial_states(X)
        constants = self.get_constants(X, train)
        preprocessed_input = self.preprocess_input(X, train)

        if train is True:
            initial_X = self.get_first_input(train=train)
            axes = [1, 0] + list(range(2, initial_X.ndim))
            initial_X = initial_X.dimshuffle(axes)
            zeros = K.zeros_like(initial_X[:1])
            initial_X = K.concatenate([zeros, initial_X[:-1]], axis=0)
            shifted_raw_inputs = initial_X.dimshuffle(axes)
            all_inputs = K.stacklists([preprocessed_input, shifted_raw_inputs])
            ndim = all_inputs.ndim
            axes = [1, 2, 0] + list(range(3, ndim))
            all_inputs = all_inputs.dimshuffle(axes)
            self.train = True
        else:
            all_inputs = preprocessed_input
            self.train = False

        last_output, outputs, states, updates = K.sampled_rnn(self.step,
                                                              all_inputs,
                                                              initial_states,
                                                              go_backwards=self.go_backwards,
                                                              mask=mask,
                                                              constants=constants)

        del self.train
        self.updates = updates

        if self.return_sequences:
            return outputs
        else:
            return last_output

    def step(self, h, states):
        prev_output = states[0][0]

        if len(states) == 2 and self.train:
            B_U = states[-1]
        elif len(states) == 1 or not self.train:
            B_U = 1
        elif len(states) > 2:
            raise Exception('States has three elements')
        else:
            raise Exception('Should either be training with dropout,' +
                            ' training without it or predicting')

        #  If training and  h has an extra dimension, that is the input form the first_layer
        #  and is used as the sampled output from the previous node
        if h.ndim > 2 and self.train:
            axes = [1, 0] + list(range(2, h.ndim))
            h = h.dimshuffle(axes)
            prev_sampled_output = h[1][:, :self.output_dim]
            h = h[0]
        #  If not training h shouldn't have an extra dimension and we need to use the actual
        #  sampled output from the previous layer
        elif h.ndim <= 2 and not self.train:
            prev_sampled_output = states[0][1]
        else:
            raise Exception('Should either be training with first layer input or predicting'+
                            ' with previous output')

        output = self.activation(h + K.dot(prev_output * B_U, self.U) +
                                     K.dot(prev_sampled_output * B_U, self.Y)
                                 )

        if self.train is True:
            final_output = output
        else:
            sampled_output = output / K.sum(output,
                                            axis=-1, keepdims=True)

            sampled_output = K.log(sampled_output) / self.temperature
            exp_sampled = K.exp(sampled_output)
            norm_exp_sampled_output = exp_sampled / K.sum(exp_sampled,
                                                          axis=-1, keepdims=True)

            rand_vector = K.random_uniform((self.input_shape[0], ))[0]
            rand_matrix = K.stacklists([rand_vector for _ in range(self.output_dim)])
            rand_matrix = K.transpose(rand_matrix)

            cumul = K.cumsum(norm_exp_sampled_output, axis=-1)
            cumul_minus = cumul - norm_exp_sampled_output
            sampled_output = K.gt(cumul, rand_matrix) * K.lt(cumul_minus, rand_matrix)

            maxes = K.argmax(sampled_output, axis=-1)
            final_output = K.to_one_hot(maxes, self.output_dim)

        output_2d_tensor = K.stacklists([output, final_output])

        return output_2d_tensor, [output_2d_tensor]


class GRU(Recurrent):
    '''Gated Recurrent Unit - Cho et al. 2014.

    # Arguments
        output_dim: dimension of the internal projections and the final output.
        init: weight initialization function.
            Can be the name of an existing function (str),
            or a Theano function (see: [initializations](../initializations.md)).
        inner_init: initialization function of the inner cells.
        activation: activation function.
            Can be the name of an existing function (str),
            or a Theano function (see: [activations](../activations.md)).
        inner_activation: activation function for the inner cells.
        W_regularizer: instance of [WeightRegularizer](../regularizers.md)
            (eg. L1 or L2 regularization), applied to the input weights matrices.
        U_regularizer: instance of [WeightRegularizer](../regularizers.md)
            (eg. L1 or L2 regularization), applied to the recurrent weights matrices.
        b_regularizer: instance of [WeightRegularizer](../regularizers.md),
            applied to the bias.
        dropout_W: float between 0 and 1. Fraction of the input units to drop for input gates.
        dropout_U: float between 0 and 1. Fraction of the input units to drop for recurrent connections.

    # References
        - [On the Properties of Neural Machine Translation: Encoder–Decoder Approaches](http://www.aclweb.org/anthology/W14-4012)
        - [Empirical Evaluation of Gated Recurrent Neural Networks on Sequence Modeling](http://arxiv.org/pdf/1412.3555v1.pdf)
        - [A Theoretically Grounded Application of Dropout in Recurrent Neural Networks](http://arxiv.org/abs/1512.05287)
    '''
    def __init__(self, output_dim,
                 init='glorot_uniform', inner_init='orthogonal',
                 activation='tanh', inner_activation='hard_sigmoid',
                 W_regularizer=None, U_regularizer=None, b_regularizer=None,
                 dropout_W=0., dropout_U=0., **kwargs):
        self.output_dim = output_dim
        self.init = initializations.get(init)
        self.inner_init = initializations.get(inner_init)
        self.activation = activations.get(activation)
        self.inner_activation = activations.get(inner_activation)
        self.W_regularizer = regularizers.get(W_regularizer)
        self.U_regularizer = regularizers.get(U_regularizer)
        self.b_regularizer = regularizers.get(b_regularizer)
        self.dropout_W, self.dropout_U = dropout_W, dropout_U
        super(GRU, self).__init__(**kwargs)

    def build(self):
        input_shape = self.input_shape
        input_dim = input_shape[2]
        self.input_dim = input_dim

        self.W_z = self.init((input_dim, self.output_dim),
                             name='{}_W_z'.format(self.name))
        self.U_z = self.inner_init((self.output_dim, self.output_dim),
                                   name='{}_U_z'.format(self.name))
        self.b_z = K.zeros((self.output_dim,), name='{}_b_z'.format(self.name))

        self.W_r = self.init((input_dim, self.output_dim),
                             name='{}_W_r'.format(self.name))
        self.U_r = self.inner_init((self.output_dim, self.output_dim),
                                   name='{}_U_r'.format(self.name))
        self.b_r = K.zeros((self.output_dim,), name='{}_b_r'.format(self.name))

        self.W_h = self.init((input_dim, self.output_dim),
                             name='{}_W_h'.format(self.name))
        self.U_h = self.inner_init((self.output_dim, self.output_dim),
                                   name='{}_U_h'.format(self.name))
        self.b_h = K.zeros((self.output_dim,), name='{}_b_h'.format(self.name))

        self.regularizers = []
        if self.W_regularizer:
            self.W_regularizer.set_param(K.concatenate([self.W_z,
                                                        self.W_r,
                                                        self.W_h]))
            self.regularizers.append(self.W_regularizer)
        if self.U_regularizer:
            self.U_regularizer.set_param(K.concatenate([self.U_z,
                                                        self.U_r,
                                                        self.U_h]))
            self.regularizers.append(self.U_regularizer)
        if self.b_regularizer:
            self.b_regularizer.set_param(K.concatenate([self.b_z,
                                                        self.b_r,
                                                        self.b_h]))
            self.regularizers.append(self.b_regularizer)

        self.trainable_weights = [self.W_z, self.U_z, self.b_z,
                                  self.W_r, self.U_r, self.b_r,
                                  self.W_h, self.U_h, self.b_h]
        if self.stateful:
            self.reset_states()
        else:
            # initial states: all-zero tensor of shape (output_dim)
            self.states = [None]

        if self.initial_weights is not None:
            self.set_weights(self.initial_weights)
            del self.initial_weights

    def reset_states(self):
        assert self.stateful, 'Layer must be stateful.'
        input_shape = self.input_shape
        if not input_shape[0]:
            raise Exception('If a RNN is stateful, a complete ' +
                            'input_shape must be provided (including batch size).')
        if hasattr(self, 'states'):
            K.set_value(self.states[0],
                        np.zeros((input_shape[0], self.output_dim)))
        else:
            self.states = [K.zeros((input_shape[0], self.output_dim))]

    def preprocess_input(self, x, train=False):
        if train and (0 < self.dropout_W < 1):
            dropout = self.dropout_W
        else:
            dropout = 0
        input_shape = self.input_shape
        input_dim = input_shape[2]
        timesteps = input_shape[1]

        x_z = time_distributed_dense(x, self.W_z, self.b_z, dropout,
                                     input_dim, self.output_dim, timesteps)
        x_r = time_distributed_dense(x, self.W_r, self.b_r, dropout,
                                     input_dim, self.output_dim, timesteps)
        x_h = time_distributed_dense(x, self.W_h, self.b_h, dropout,
                                     input_dim, self.output_dim, timesteps)
        return K.concatenate([x_z, x_r, x_h], axis=2)

    def step(self, x, states):
        h_tm1 = states[0]  # previous memory
        if len(states) == 2:
            B_U = states[1]  # dropout matrices for recurrent units
        else:
            B_U = [1., 1., 1.]

        x_z = x[:, :self.output_dim]
        x_r = x[:, self.output_dim: 2 * self.output_dim]
        x_h = x[:, 2 * self.output_dim:]

        z = self.inner_activation(x_z + K.dot(h_tm1 * B_U[0], self.U_z))
        r = self.inner_activation(x_r + K.dot(h_tm1 * B_U[1], self.U_r))

        hh = self.activation(x_h + K.dot(r * h_tm1 * B_U[2], self.U_h))
        h = z * h_tm1 + (1 - z) * hh
        return h, [h]

    def get_constants(self, x, train=False):
        if train and (0 < self.dropout_U < 1):
            ones = K.ones_like(K.reshape(x[:, 0, 0], (-1, 1)))
            ones = K.concatenate([ones] * self.output_dim, 1)
            B_U = [K.dropout(ones, self.dropout_U) for _ in range(3)]
            return [B_U]
        return []

    def get_config(self):
        config = {"output_dim": self.output_dim,
                  "init": self.init.__name__,
                  "inner_init": self.inner_init.__name__,
                  "activation": self.activation.__name__,
                  "inner_activation": self.inner_activation.__name__,
                  "W_regularizer": self.W_regularizer.get_config() if self.W_regularizer else None,
                  "U_regularizer": self.U_regularizer.get_config() if self.U_regularizer else None,
                  "b_regularizer": self.b_regularizer.get_config() if self.b_regularizer else None,
                  "dropout_W": self.dropout_W,
                  "dropout_U": self.dropout_U}
        base_config = super(GRU, self).get_config()
        return dict(list(base_config.items()) + list(config.items()))


class TerminalGRU(GRU):
    '''GRU where the one-hot output of each neuron is fed into the next.
    In training it uses the actual training data, in testing it uses the multinomial
    sampled output of the previous neuron.

    '''
    def __init__(self, output_dim, temperature=1, **kwargs):
        super(TerminalGRU, self).__init__(output_dim, **kwargs)
        self.temperature = temperature

    def build(self):
        self.Y = self.inner_init((self.output_dim, self.output_dim),
                                 name='{}_Y'.format(self.name))
        super(TerminalGRU, self).build()

        self.trainable_weights += [self.Y]

    def get_initial_states(self, x):
        # build an all-zero tensor of shape (samples, output_dim)
        initial_state = K.zeros_like(x)  # (samples, timesteps, input_dim)
        initial_state = K.sum(initial_state, axis=1)  # (samples, input_dim)
        reducer = K.zeros((self.input_dim, self.output_dim))
        initial_state = K.dot(initial_state, reducer)  # (samples, output_dim)
        initial_states = [(initial_state, initial_state) for _ in range(len(self.states))]
        return initial_states

    def get_constants(self, x, train=False):
        if train and (0 < self.dropout_U < 1):
            ones = K.ones_like(K.reshape(x[:, 0, 0], (-1, 1)))
            ones = K.concatenate([ones] * self.output_dim, 1)
            B_U = [K.dropout(ones, self.dropout_U) for _ in range(4)]
            return [B_U]
        return []

    def get_output(self, train=False):
        # input shape: (nb_samples, time (padded with zeros), input_dim)
        X = self.get_input(train)
        mask = self.get_input_mask(train)

        assert K.ndim(X) == 3

        if self.stateful:
            initial_states = self.states
        else:
            initial_states = self.get_initial_states(X)
        constants = self.get_constants(X, train)
        preprocessed_input = self.preprocess_input(X, train)

        if train is True:
            initial_X = self.get_first_input(train=train)
            axes = [1, 0] + list(range(2, initial_X.ndim))
            initial_X = initial_X.dimshuffle(axes)
            zeros = K.zeros_like(initial_X[:1])
            initial_X = K.concatenate([zeros, initial_X[:-1]], axis=0)
            shifted_raw_inputs = initial_X.dimshuffle(axes)
            ## Silly concatenate to have same dimension as preprocessed inputs 3xoutput_dim
            shifted_raw_inputs = K.concatenate([shifted_raw_inputs,
                                                shifted_raw_inputs,
                                                shifted_raw_inputs], axis=2)
            all_inputs = K.stacklists([preprocessed_input, shifted_raw_inputs])
            ndim = all_inputs.ndim
            axes = [1, 2, 0] + list(range(3, ndim))
            all_inputs = all_inputs.dimshuffle(axes)
            self.train = True
        else:
            all_inputs = preprocessed_input
            self.train = False

        last_output, outputs, states, updates = K.sampled_rnn(self.step,
                                                              all_inputs,
                                                              initial_states,
                                                              go_backwards=self.go_backwards,
                                                              mask=mask,
                                                              constants=constants)

        del self.train
        self.updates = updates

        if self.return_sequences:
            return outputs
        else:
            return last_output

    def step(self, h, states):
        prev_output = states[0][0]

        if len(states) == 2 and self.train:
            B_U = states[-1]
        elif len(states) == 1 or not self.train:
            B_U = [1., 1., 1., 1.]
        elif len(states) > 2:
            raise Exception('States has three elements')
        else:
            raise Exception('Should either be training with dropout,' +
                            ' training without it or predicting')

        #  If training and  h has an extra dimension, that is the input form the first_layer
        #  and is used as the sampled output from the previous node
        if h.ndim > 2 and self.train:
            axes = [1, 0] + list(range(2, h.ndim))
            h = h.dimshuffle(axes)
            prev_sampled_output = h[1][:, :self.output_dim]
            h = h[0]
        #  If not training h shouldn't have an extra dimension and we need to use the actual
        #  sampled output from the previous layer
        elif h.ndim <= 2 and not self.train:
            prev_sampled_output = states[0][1]
        else:
            raise Exception('Should either be training with first layer input or predicting'+
                            ' with previous output')

        x_z = h[:, :self.output_dim]
        x_r = h[:, self.output_dim: 2 * self.output_dim]
        x_h = h[:, 2 * self.output_dim:]

        z = self.inner_activation(x_z + K.dot(prev_output * B_U[0], self.U_z))
        r = self.inner_activation(x_r + K.dot(prev_output * B_U[1], self.U_r))

        hh = self.activation(x_h +
                             K.dot(r * prev_output * B_U[2], self.U_h) +
                             K.dot(r * prev_sampled_output * B_U[3], self.Y) )
        output = z * prev_output + (1. - z) * hh

        if self.train is True:
            final_output = output
        else:
            sampled_output = output / K.sum(output,
                                            axis=-1, keepdims=True)

            sampled_output = K.log(sampled_output) / self.temperature
            exp_sampled = K.exp(sampled_output)
            norm_exp_sampled_output = exp_sampled / K.sum(exp_sampled,
                                                          axis=-1, keepdims=True)

            rand_vector = K.random_uniform((self.input_shape[0], ))[0]
            rand_matrix = K.stacklists([rand_vector for _ in range(self.output_dim)])
            rand_matrix = K.transpose(rand_matrix)

            cumul = K.cumsum(norm_exp_sampled_output, axis=-1)
            cumul_minus = cumul - norm_exp_sampled_output
            sampled_output = K.gt(cumul, rand_matrix) * K.lt(cumul_minus, rand_matrix)

            maxes = K.argmax(sampled_output, axis=-1)
            final_output = K.to_one_hot(maxes, self.output_dim)

        output_2d_tensor = K.stacklists([output, final_output])

        return output_2d_tensor, [output_2d_tensor]


class LSTM(Recurrent):
    '''Long-Short Term Memory unit - Hochreiter 1997.

    For a step-by-step description of the algorithm, see
    [this tutorial](http://deeplearning.net/tutorial/lstm.html).

    # Arguments
        output_dim: dimension of the internal projections and the final output.
        init: weight initialization function.
            Can be the name of an existing function (str),
            or a Theano function (see: [initializations](../initializations.md)).
        inner_init: initialization function of the inner cells.
        forget_bias_init: initialization function for the bias of the forget gate.
            [Jozefowicz et al.](http://www.jmlr.org/proceedings/papers/v37/jozefowicz15.pdf)
            recommend initializing with ones.
        activation: activation function.
            Can be the name of an existing function (str),
            or a Theano function (see: [activations](../activations.md)).
        inner_activation: activation function for the inner cells.
        W_regularizer: instance of [WeightRegularizer](../regularizers.md)
            (eg. L1 or L2 regularization), applied to the input weights matrices.
        U_regularizer: instance of [WeightRegularizer](../regularizers.md)
            (eg. L1 or L2 regularization), applied to the recurrent weights matrices.
        b_regularizer: instance of [WeightRegularizer](../regularizers.md),
            applied to the bias.
        dropout_W: float between 0 and 1. Fraction of the input units to drop for input gates.
        dropout_U: float between 0 and 1. Fraction of the input units to drop for recurrent connections.

    # References
        - [Long short-term memory](http://deeplearning.cs.cmu.edu/pdfs/Hochreiter97_lstm.pdf) (original 1997 paper)
        - [Learning to forget: Continual prediction with LSTM](http://www.mitpressjournals.org/doi/pdf/10.1162/089976600300015015)
        - [Supervised sequence labelling with recurrent neural networks](http://www.cs.toronto.edu/~graves/preprint.pdf)
        - [A Theoretically Grounded Application of Dropout in Recurrent Neural Networks](http://arxiv.org/abs/1512.05287)
    '''
    def __init__(self, output_dim,
                 init='glorot_uniform', inner_init='orthogonal',
                 forget_bias_init='one', activation='tanh',
                 inner_activation='hard_sigmoid',
                 W_regularizer=None, U_regularizer=None, b_regularizer=None,
                 dropout_W=0., dropout_U=0., **kwargs):
        self.output_dim = output_dim
        self.init = initializations.get(init)
        self.inner_init = initializations.get(inner_init)
        self.forget_bias_init = initializations.get(forget_bias_init)
        self.activation = activations.get(activation)
        self.inner_activation = activations.get(inner_activation)
        self.W_regularizer = regularizers.get(W_regularizer)
        self.U_regularizer = regularizers.get(U_regularizer)
        self.b_regularizer = regularizers.get(b_regularizer)
        self.dropout_W, self.dropout_U = dropout_W, dropout_U
        super(LSTM, self).__init__(**kwargs)

    def build(self):
        input_shape = self.input_shape
        input_dim = input_shape[2]
        self.input_dim = input_dim

        if self.stateful:
            self.reset_states()
        else:
            # initial states: 2 all-zero tensors of shape (output_dim)
            self.states = [None, None]

        self.W_i = self.init((input_dim, self.output_dim),
                             name='{}_W_i'.format(self.name))
        self.U_i = self.inner_init((self.output_dim, self.output_dim),
                                   name='{}_U_i'.format(self.name))
        self.b_i = K.zeros((self.output_dim,), name='{}_b_i'.format(self.name))

        self.W_f = self.init((input_dim, self.output_dim),
                             name='{}_W_f'.format(self.name))
        self.U_f = self.inner_init((self.output_dim, self.output_dim),
                                   name='{}_U_f'.format(self.name))
        self.b_f = self.forget_bias_init((self.output_dim,),
                                         name='{}_b_f'.format(self.name))

        self.W_c = self.init((input_dim, self.output_dim),
                             name='{}_W_c'.format(self.name))
        self.U_c = self.inner_init((self.output_dim, self.output_dim),
                                   name='{}_U_c'.format(self.name))
        self.b_c = K.zeros((self.output_dim,), name='{}_b_c'.format(self.name))

        self.W_o = self.init((input_dim, self.output_dim),
                             name='{}_W_o'.format(self.name))
        self.U_o = self.inner_init((self.output_dim, self.output_dim),
                                   name='{}_U_o'.format(self.name))
        self.b_o = K.zeros((self.output_dim,), name='{}_b_o'.format(self.name))

        self.regularizers = []
        if self.W_regularizer:
            self.W_regularizer.set_param(K.concatenate([self.W_i,
                                                        self.W_f,
                                                        self.W_c,
                                                        self.W_o]))
            self.regularizers.append(self.W_regularizer)
        if self.U_regularizer:
            self.U_regularizer.set_param(K.concatenate([self.U_i,
                                                        self.U_f,
                                                        self.U_c,
                                                        self.U_o]))
            self.regularizers.append(self.U_regularizer)
        if self.b_regularizer:
            self.b_regularizer.set_param(K.concatenate([self.b_i,
                                                        self.b_f,
                                                        self.b_c,
                                                        self.b_o]))
            self.regularizers.append(self.b_regularizer)

        self.trainable_weights = [self.W_i, self.U_i, self.b_i,
                                  self.W_c, self.U_c, self.b_c,
                                  self.W_f, self.U_f, self.b_f,
                                  self.W_o, self.U_o, self.b_o]

        if self.initial_weights is not None:
            self.set_weights(self.initial_weights)
            del self.initial_weights

    def reset_states(self):
        assert self.stateful, 'Layer must be stateful.'
        input_shape = self.input_shape
        if not input_shape[0]:
            raise Exception('If a RNN is stateful, a complete ' +
                            'input_shape must be provided (including batch size).')
        if hasattr(self, 'states'):
            K.set_value(self.states[0],
                        np.zeros((input_shape[0], self.output_dim)))
            K.set_value(self.states[1],
                        np.zeros((input_shape[0], self.output_dim)))
        else:
            self.states = [K.zeros((input_shape[0], self.output_dim)),
                           K.zeros((input_shape[0], self.output_dim))]

    def preprocess_input(self, x, train=False):
        if train and (0 < self.dropout_W < 1):
            dropout = self.dropout_W
        else:
            dropout = 0
        input_shape = self.input_shape
        input_dim = input_shape[2]
        timesteps = input_shape[1]

        x_i = time_distributed_dense(x, self.W_i, self.b_i, dropout,
                                     input_dim, self.output_dim, timesteps)
        x_f = time_distributed_dense(x, self.W_f, self.b_f, dropout,
                                     input_dim, self.output_dim, timesteps)
        x_c = time_distributed_dense(x, self.W_c, self.b_c, dropout,
                                     input_dim, self.output_dim, timesteps)
        x_o = time_distributed_dense(x, self.W_o, self.b_o, dropout,
                                     input_dim, self.output_dim, timesteps)
        return K.concatenate([x_i, x_f, x_c, x_o], axis=2)

    def step(self, x, states):
        h_tm1 = states[0]
        c_tm1 = states[1]
        if len(states) == 3:
            B_U = states[2]
        else:
            B_U = [1. for _ in range(4)]

        x_i = x[:, :self.output_dim]
        x_f = x[:, self.output_dim: 2 * self.output_dim]
        x_c = x[:, 2 * self.output_dim: 3 * self.output_dim]
        x_o = x[:, 3 * self.output_dim:]

        i = self.inner_activation(x_i + K.dot(h_tm1 * B_U[0], self.U_i))
        f = self.inner_activation(x_f + K.dot(h_tm1 * B_U[1], self.U_f))
        c = f * c_tm1 + i * self.activation(x_c + K.dot(h_tm1 * B_U[2], self.U_c))
        o = self.inner_activation(x_o + K.dot(h_tm1 * B_U[3], self.U_o))

        h = o * self.activation(c)
        return h, [h, c]

    def get_constants(self, x, train=False):
        if train and (0 < self.dropout_U < 1):
            ones = K.ones_like(K.reshape(x[:, 0, 0], (-1, 1)))
            ones = K.concatenate([ones] * self.output_dim, 1)
            B_U = [K.dropout(ones, self.dropout_U) for _ in range(4)]
            return [B_U]
        return []

    def get_config(self):
        config = {"output_dim": self.output_dim,
                  "init": self.init.__name__,
                  "inner_init": self.inner_init.__name__,
                  "forget_bias_init": self.forget_bias_init.__name__,
                  "activation": self.activation.__name__,
                  "inner_activation": self.inner_activation.__name__,
                  "W_regularizer": self.W_regularizer.get_config() if self.W_regularizer else None,
                  "U_regularizer": self.U_regularizer.get_config() if self.U_regularizer else None,
                  "b_regularizer": self.b_regularizer.get_config() if self.b_regularizer else None,
                  "dropout_W": self.dropout_W,
                  "dropout_U": self.dropout_U}
        base_config = super(LSTM, self).get_config()
        return dict(list(base_config.items()) + list(config.items()))
