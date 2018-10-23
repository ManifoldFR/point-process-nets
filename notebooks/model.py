"""
Models for the Neural Hawkes process.
"""
import torch
from torch import nn
import pdb

device = torch.device('cpu')


class NeuralCTLSTM(nn.Module):
    """
    A continuous-time LSTM, defined according to Eisner & Mei's article
    https://arxiv.org/abs/1612.09328
    Batch size of all tensors must be the first dimension.
    """

    def __init__(self, hidden_dim: int):
        super(NeuralCTLSTM, self).__init__()

        self.hidden_dim = hidden_dim

        self.input_g = nn.Linear(hidden_dim, hidden_dim)
        self.forget_g = nn.Linear(hidden_dim, hidden_dim)
        self.output_g = nn.Linear(hidden_dim, hidden_dim)

        self.ibar = nn.Linear(hidden_dim, hidden_dim)
        self.fbar = nn.Linear(hidden_dim, hidden_dim)

        # activation will be tanh
        self.z_gate = nn.Linear(hidden_dim, hidden_dim)

        # Cell decay factor
        self.decay = nn.Linear(hidden_dim, hidden_dim)
        # we can learn the parameters of this
        self.decay_act = nn.Softplus()

        # The hidden state contains
        # the cell state at t, the target cell state
        self.init_hidden()

        self.activation = nn.Softplus()
        self.weight_f = torch.rand(self.hidden_dim, device=device)

    def init_hidden(self, batch_size=1):
        """
        Initialize the hidden state, and the two hidden memory
        cells c and cbar.
        The first dimension is the batch size.
        """
        self.hidden = (torch.rand(batch_size, self.hidden_dim, device=device),
                       torch.rand(batch_size, self.hidden_dim, device=device),
                       torch.rand(batch_size, self.hidden_dim, device=device))

    def c_func(self, dt: torch.Tensor, c: torch.Tensor,
               cbar: torch.Tensor, decay: torch.Tensor):
        """
        Compute the decayed cell memory c(t) = c(ti + dt)
        """
        # print("Computing decayed cell memory...")
        # print(c.shape, type(c))
        # print(cbar.shape, type(cbar))
        # print(decay.shape, type(decay))
        # print(dt, type(dt))
        dt_expd = dt.unsqueeze(-1).expand(c.shape)
        return cbar + (c - cbar) * torch.exp(-decay * dt_expd)

    def next_event(self, output, dt, decay):
        # h_ti, c_ti, cbar = self.hidden
        # c_t_after = self.c_func(dt, c_ti, cbar, decay)
        # h_t_after = output * torch.tanh(c_t_after)
        # lbdaMax = h_t_after
        raise NotImplementedError

    def forward(self, inter_times):
        """
        inter_times: inter-arrival time for the next event in the sequence

        Returns:
            output : result of the output gate
            h_ti   : hidden state
            c_ti   : cell state
            cbar   : cell target
            decay_t: decay parameter on the interval
        #TODO event type embedding
        """
        # get the hidden state and memory from before
        h_ti, c_ti, cbar = self.hidden

        # TODO concatenate event embedding with ht
        v = torch.cat((h_ti,))
        input = torch.sigmoid(self.input_g(v))
        forget = torch.sigmoid(self.forget_g(v))
        output = torch.sigmoid(self.output_g(v))

        input_target = torch.sigmoid(self.ibar(v))
        forget_target = torch.sigmoid(self.fbar(v))

        # Not-quite-c
        zi = torch.tanh(self.z_gate(v))

        # Compute the decay parameter
        decay_t = self.decay_act(self.decay(v))

        # Now update the cell memory
        # Decay the cell memory
        c_t_after = self.c_func(inter_times, c_ti, cbar, decay_t)
        # Update the cell
        c_ti = forget * c_t_after + input * zi
        # Update the cell state asymptotic value
        cbar = forget_target * cbar + input_target * zi
        h_ti = output * torch.tanh(c_t_after)

        # Store our new states for the next pass to use
        self.hidden = h_ti, c_ti, cbar
        return output, h_ti, c_ti, cbar, decay_t

    def eval_intensity(self, dt: torch.Tensor, output: torch.Tensor,
                       c_ti, cbar, decay):
        """
        Compute the intensity function
        Args:
            dt:     time increments array
                    dt[i] is the time elapsed since event t_i
                    verify that if you want to compute at time t,
                    t_i <= t <= t_{i+1}, then dt[i] = t - t_i
            output: NN output o_i
            c_ti:   previous cell state
            cbar:   previous cell target
            decay:  decay[i] is the degrowth param. on range [t_i, t_{i+1}]

        It is best to store the training history in variables for this.
        """
        # Get the updated c(t)
        c_t_after = self.c_func(dt, c_ti, cbar, decay)
        h_t = output * torch.tanh(c_t_after)
        batch_size = h_t.size(0)
        try:
            hidden_size = self.weight_f.size(0)
            weight_f = (
                self.weight_f.expand(batch_size, hidden_size).unsqueeze(1)
            )
            pre_lambda = torch.bmm(weight_f, h_t.transpose(2, 1)).squeeze(1)
        except BaseException:
            print("Error occured in c_func")
            print(" dt shape %s" % str(dt.shape))
            print(" Weights shape %s" % str(self.weight_f.shape))
            print(" h_t shape %s" % str(h_t.shape))

            raise
        return self.activation(pre_lambda)

    def likelihood(self, event_times, cell_hist, cell_target_hist,
                   output_hist, decay_hist, T):
        """
        Compute the negative log-likelihood as a loss function
        #lengths: real sequence lengths
        c_ti :  entire cell state history
        output: entire output history
        decay:  entire decay history
        """
        inter_times = event_times[:, -1:] - event_times[:, 1:]
        # Get the intensity process
        event_intensities = self.eval_intensity(
            inter_times, output_hist,
            cell_hist, cell_target_hist, decay_hist)
        first_sum = event_intensities.log().sum(dim=1)
        # The integral term is computed using a Monte Carlo method
        batch_size = event_times.size(0)
        input_length = event_times.size(1)
        # random samples in [0, T]
        # dim (batch_size) * (input_length)
        # each sample belongs to
        samples: torch.Tensor = inter_times * torch.rand_like(inter_times)

        # Get 
        lam_samples = self.eval_intensity(
            samples, output_hist,
            cell_hist, cell_target_hist, decay_hist)
        integral = lam_samples.sum(dim=1)
        # Tensor of dim. batch_size
        # of the values of the likelihood
        res = first_sum - integral
        # return the opposite of the mean
        return -res.mean()