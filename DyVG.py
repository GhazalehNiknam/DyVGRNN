#!/usr/bin/env python
# coding: utf-8
# In[1]:

##For running first 
# !pip install torch_sparse torch-cluster torch-geometric==1.0.2 torch_scatter torch-geometric torch-spline-conv torchvision

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

# import wandb
# wandb.init(mode="disabled")

import math
import numpy as np
import torch
import torch.nn as nn
import torch.utils
import torch.utils.data
from torchvision import datasets, transforms
from torch.autograd import Variable
import matplotlib.pyplot as plt 
from scipy.ndimage import rotate
from torch.distributions.uniform import Uniform
from torch.distributions.normal import Normal
from sklearn.datasets import fetch_openml
# from torch_geometric import nn as tgnn
from input_data import load_data
from preprocessing import preprocess_graph, construct_feed_dict, sparse_to_tuple, mask_test_edges
import scipy.sparse as sp
from scipy.linalg import block_diag
from torch.nn.parameter import Parameter
from torch.nn.modules.module import Module
import tarfile
import torch.nn.functional as F
import copy
import time
from torch_scatter import scatter_mean, scatter_max, scatter_add
from torch_geometric.utils import remove_self_loops,add_self_loops
from torch_geometric.datasets import Planetoid
import networkx as nx
import scipy.io as sio
import torch_scatter
import inspect
from sklearn.metrics import roc_auc_score
from sklearn.metrics import average_precision_score
import copy
import pickle


# In[2]:


seed = 123
np.random.seed(seed)


# In[3]:


# utility functions

def uniform(size, tensor):
    stdv = 1.0 / math.sqrt(size)
    if tensor is not None:
        tensor.data.uniform_(-stdv, stdv)


def glorot(tensor):
    stdv = math.sqrt(6.0 / (tensor.size(0) + tensor.size(1)))
    if tensor is not None:
        tensor.data.uniform_(-stdv, stdv)


def zeros(tensor):
    if tensor is not None:
        tensor.data.fill_(0)


def ones(tensor):
    if tensor is not None:
        tensor.data.fill_(1)


def reset(nn):
    def _reset(item):
        if hasattr(item, 'reset_parameters'):
            item.reset_parameters()

    if nn is not None:
        if hasattr(nn, 'children') and len(list(nn.children())) > 0:
            for item in nn.children():
                _reset(item)
        else:
            _reset(nn)


def scatter_(name, src, index, dim_size=None):
    r"""Aggregates all values from the :attr:`src` tensor at the indices
    specified in the :attr:`index` tensor along the first dimension.
    If multiple indices reference the same location, their contributions
    are aggregated according to :attr:`name` (either :obj:`"add"`,
    :obj:`"mean"` or :obj:`"max"`).
    Args:
        name (string): The aggregation to use (:obj:`"add"`, :obj:`"mean"`,
            :obj:`"max"`).
        src (Tensor): The source tensor.
        index (LongTensor): The indices of elements to scatter.
        dim_size (int, optional): Automatically create output tensor with size
            :attr:`dim_size` in the first dimension. If set to :attr:`None`, a
            minimal sized output tensor is returned. (default: :obj:`None`)
    :rtype: :class:`Tensor`
    """

    assert name in ['add', 'mean', 'max']

    op = getattr(torch_scatter, 'scatter_{}'.format(name))
    fill_value = -1e38 if name is 'max' else 0

    # out = op(src, index, 0, None, dim_size, fill_value)

    # print(src.shape,index.shape)
    # res_shape = index.shape[:1] + src.shape[1:]
    # src=src.reshape(src.shape[0], -1)
    # index=index.reshape(src.shape[0],)
    # src=src.reshape(1,src.shape[0], src.shape[1],src.shape[2])
    
    out = op(src, index, 0, None, dim_size)
    # print(out)
    if isinstance(out, tuple):
        out = out[0]

    if name is 'max':
        out[out == fill_value] = 0

    return out


class MessagePassing(torch.nn.Module):
    r"""Base class for creating message passing layers
    .. math::
        \mathbf{x}_i^{\prime} = \gamma_{\mathbf{\Theta}} \left( \mathbf{x}_i,
        \square_{j \in \mathcal{N}(i)} \, \phi_{\mathbf{\Theta}}
        \left(\mathbf{x}_i, \mathbf{x}_j,\mathbf{e}_{i,j}\right) \right),
    where :math:`\square` denotes a differentiable, permutation invariant
    function, *e.g.*, sum, mean or max, and :math:`\gamma_{\mathbf{\Theta}}`
    and :math:`\phi_{\mathbf{\Theta}}` denote differentiable functions such as
    MLPs.
    See `here <https://rusty1s.github.io/pytorch_geometric/build/html/notes/
    create_gnn.html>`__ for the accompanying tutorial.
    """

    def __init__(self, aggr='add'):
        super(MessagePassing, self).__init__()

        self.message_args = inspect.getargspec(self.message)[0][1:]
        self.update_args = inspect.getargspec(self.update)[0][2:]

    def propagate(self, aggr, edge_index, **kwargs):
        r"""The initial call to start propagating messages.
        Takes in an aggregation scheme (:obj:`"add"`, :obj:`"mean"` or
        :obj:`"max"`), the edge indices, and all additional data which is
        needed to construct messages and to update node embeddings."""

        assert aggr in ['add', 'mean', 'max']
        kwargs['edge_index'] = edge_index

        size = None
        message_args = []
        for arg in self.message_args:
            if arg[-2:] == '_i':
                tmp = kwargs[arg[:-2]]
                size = tmp.size(0)
                message_args.append(tmp[edge_index[0]])
            elif arg[-2:] == '_j':
                tmp = kwargs[arg[:-2]]
                size = tmp.size(0)
                message_args.append(tmp[edge_index[1]])
            else:
                message_args.append(kwargs[arg])

        update_args = [kwargs[arg] for arg in self.update_args]

        out = self.message(*message_args)
        out = scatter_(aggr, out, edge_index[0], dim_size=size)
        out = self.update(out, *update_args)

        return out

    def message(self, x_j):  # pragma: no cover
        r"""Constructs messages in analogy to :math:`\phi_{\mathbf{\Theta}}`
        for each edge in :math:`(i,j) \in \mathcal{E}`.
        Can take any argument which was initially passed to :meth:`propagate`.
        In addition, features can be lifted to the source node :math:`i` and
        target node :math:`j` by appending :obj:`_i` or :obj:`_j` to the
        variable name, *.e.g.* :obj:`x_i` and :obj:`x_j`."""

        return x_j

    def update(self, aggr_out):  # pragma: no cover
        r"""Updates node embeddings in analogy to
        :math:`\gamma_{\mathbf{\Theta}}` for each node
        :math:`i \in \mathcal{V}`.
        Takes in the output of aggregation as first argument and any argument
        which was initially passed to :meth:`propagate`."""

        return aggr_out


def tuple_to_array(lot):
    out = np.array(list(lot[0]))
    for i in range(1, len(lot)):
        out = np.vstack((out, np.array(list(lot[i]))))
    
    return out


# In[4]:


# masking functions

def mask_edges_det(adjs_list):
    adj_train_l, train_edges_l, val_edges_l = [], [], []
    val_edges_false_l, test_edges_l, test_edges_false_l = [], [], []
    edges_list = []
    for i in range(0, len(adjs_list)):
        # Function to build test set with 10% positive links
        # NOTE: Splits are randomized and results might slightly deviate from reported numbers in the paper.
        
        adj = adjs_list[i]
        # Remove diagonal elements
        adj = adj - sp.dia_matrix((adj.diagonal()[np.newaxis, :], [0]), shape=adj.shape)
        adj.eliminate_zeros()
        # Check that diag is zero:
        assert np.diag(adj.todense()).sum() == 0
        
        adj_triu = sp.triu(adj)
        adj_tuple = sparse_to_tuple(adj_triu)
        edges = adj_tuple[0]
        edges_all = sparse_to_tuple(adj)[0]
        num_test = int(np.floor(edges.shape[0] / 10.))
        num_val = int(np.floor(edges.shape[0] / 20.))
        
        all_edge_idx = list(range(edges.shape[0]))
        np.random.shuffle(all_edge_idx)
        val_edge_idx = all_edge_idx[:num_val]
        test_edge_idx = all_edge_idx[num_val:(num_val + num_test)]
        test_edges = edges[test_edge_idx]
        val_edges = edges[val_edge_idx]
        train_edges = np.delete(edges, np.hstack([test_edge_idx, val_edge_idx]), axis=0)
        
        edges_list.append(edges)
        
        def ismember(a, b, tol=5):
            rows_close = np.all(np.round(a - b[:, None], tol) == 0, axis=-1)
            return np.any(rows_close)

        test_edges_false = []
        while len(test_edges_false) < len(test_edges):
            idx_i = np.random.randint(0, adj.shape[0])
            idx_j = np.random.randint(0, adj.shape[0])
            if idx_i == idx_j:
                continue
            if ismember([idx_i, idx_j], edges_all):
                continue
            if test_edges_false:
                if ismember([idx_j, idx_i], np.array(test_edges_false)):
                    continue
                if ismember([idx_i, idx_j], np.array(test_edges_false)):
                    continue
            test_edges_false.append([idx_i, idx_j])

        val_edges_false = []
        while len(val_edges_false) < len(val_edges):
            idx_i = np.random.randint(0, adj.shape[0])
            idx_j = np.random.randint(0, adj.shape[0])
            if idx_i == idx_j:
                continue
            if ismember([idx_i, idx_j], train_edges):
                continue
            if ismember([idx_j, idx_i], train_edges):
                continue
            if ismember([idx_i, idx_j], val_edges):
                continue
            if ismember([idx_j, idx_i], val_edges):
                continue
            if val_edges_false:
                if ismember([idx_j, idx_i], np.array(val_edges_false)):
                    continue
                if ismember([idx_i, idx_j], np.array(val_edges_false)):
                    continue
            val_edges_false.append([idx_i, idx_j])

        assert ~ismember(test_edges_false, edges_all)
        assert ~ismember(val_edges_false, edges_all)
        assert ~ismember(val_edges, train_edges)
        assert ~ismember(test_edges, train_edges)
        assert ~ismember(val_edges, test_edges)

        data = np.ones(train_edges.shape[0])

        # Re-build adj matrix
        adj_train = sp.csr_matrix((data, (train_edges[:, 0], train_edges[:, 1])), shape=adj.shape)
        adj_train = adj_train + adj_train.T

        adj_train_l.append(adj_train)
        train_edges_l.append(train_edges)
        val_edges_l.append(val_edges)
        val_edges_false_l.append(val_edges_false)
        test_edges_l.append(test_edges)
        test_edges_false_l.append(test_edges_false)

    # NOTE: these edge lists only contain single direction of edge!
    return adj_train_l, train_edges_l, val_edges_l, val_edges_false_l, test_edges_l, test_edges_false_l

def mask_edges_prd(adjs_list):
    pos_edges_l , false_edges_l = [], []
    edges_list = []
    for i in range(0, len(adjs_list)):
        # Function to build test set with 10% positive links
        # NOTE: Splits are randomized and results might slightly deviate from reported numbers in the paper.
        
        adj = adjs_list[i]
        # Remove diagonal elements
        adj = adj - sp.dia_matrix((adj.diagonal()[np.newaxis, :], [0]), shape=adj.shape)
        adj.eliminate_zeros()
        # Check that diag is zero:
        assert np.diag(adj.todense()).sum() == 0
        
        adj_triu = sp.triu(adj)
        adj_tuple = sparse_to_tuple(adj_triu)
        edges = adj_tuple[0]
        edges_all = sparse_to_tuple(adj)[0]
        num_false = int(edges.shape[0])
        
        pos_edges_l.append(edges)
        
        def ismember(a, b, tol=5):
            rows_close = np.all(np.round(a - b[:, None], tol) == 0, axis=-1)
            return np.any(rows_close)
        
        edges_false = []
        while len(edges_false) < num_false:
            idx_i = np.random.randint(0, adj.shape[0])
            idx_j = np.random.randint(0, adj.shape[0])
            if idx_i == idx_j:
                continue
            if ismember([idx_i, idx_j], edges_all):
                continue
            if edges_false:
                if ismember([idx_j, idx_i], np.array(edges_false)):
                    continue
                if ismember([idx_i, idx_j], np.array(edges_false)):
                    continue
            edges_false.append([idx_i, idx_j])

        assert ~ismember(edges_false, edges_all)
        
        false_edges_l.append(edges_false)

    # NOTE: these edge lists only contain single direction of edge!
    return pos_edges_l, false_edges_l

def mask_edges_prd_new(adjs_list, adj_orig_dense_list):
    pos_edges_l , false_edges_l = [], []
    edges_list = []
    
    # Function to build test set with 10% positive links
    # NOTE: Splits are randomized and results might slightly deviate from reported numbers in the paper.

    adj = adjs_list[0]
    # Remove diagonal elements
    adj = adj - sp.dia_matrix((adj.diagonal()[np.newaxis, :], [0]), shape=adj.shape)
    adj.eliminate_zeros()
    # Check that diag is zero:
    assert np.diag(adj.todense()).sum() == 0

    adj_triu = sp.triu(adj)
    adj_tuple = sparse_to_tuple(adj_triu)
    edges = adj_tuple[0]
    edges_all = sparse_to_tuple(adj)[0]
    num_false = int(edges.shape[0])

    pos_edges_l.append(edges)

    def ismember(a, b, tol=5):
        rows_close = np.all(np.round(a - b[:, None], tol) == 0, axis=-1)
        return np.any(rows_close)

    edges_false = []
    while len(edges_false) < num_false:
        idx_i = np.random.randint(0, adj.shape[0])
        idx_j = np.random.randint(0, adj.shape[0])
        if idx_i == idx_j:
            continue
        if ismember([idx_i, idx_j], edges_all):
            continue
        if edges_false:
            if ismember([idx_j, idx_i], np.array(edges_false)):
                continue
            if ismember([idx_i, idx_j], np.array(edges_false)):
                continue
        edges_false.append([idx_i, idx_j])

    assert ~ismember(edges_false, edges_all)    
    false_edges_l.append(np.asarray(edges_false))
    
    for i in range(1, len(adjs_list)):
        edges_pos = np.transpose(np.asarray(np.where((adj_orig_dense_list[i] - adj_orig_dense_list[i-1])>0)))
        num_false = int(edges_pos.shape[0])
        
        adj = adjs_list[i]
        # Remove diagonal elements
        adj = adj - sp.dia_matrix((adj.diagonal()[np.newaxis, :], [0]), shape=adj.shape)
        adj.eliminate_zeros()
        # Check that diag is zero:
        assert np.diag(adj.todense()).sum() == 0
        
        adj_triu = sp.triu(adj)
        adj_tuple = sparse_to_tuple(adj_triu)
        edges = adj_tuple[0]
        edges_all = sparse_to_tuple(adj)[0]
        
        edges_false = []
        while len(edges_false) < num_false:
            idx_i = np.random.randint(0, adj.shape[0])
            idx_j = np.random.randint(0, adj.shape[0])
            if idx_i == idx_j:
                continue
            if ismember([idx_i, idx_j], edges_all):
                continue
            if edges_false:
                if ismember([idx_j, idx_i], np.array(edges_false)):
                    continue
                if ismember([idx_i, idx_j], np.array(edges_false)):
                    continue
            edges_false.append([idx_i, idx_j])
        
        assert ~ismember(edges_false, edges_all)
        
        false_edges_l.append(np.asarray(edges_false))
        pos_edges_l.append(edges_pos)
    
    # NOTE: these edge lists only contain single direction of edge!
    return pos_edges_l, false_edges_l


# In[5]:


# loading data

# Enron dataset
with open('data/enron10/adj_time_list.pickle', 'rb') as handle:
    adj_time_list = pickle.load(handle,encoding="latin1")

with open('data/enron10/adj_orig_dense_list.pickle', 'rb') as handle:
    adj_orig_dense_list = pickle.load(handle,encoding='bytes')


# COLAB dataset
# with open('data/dblp/adj_time_list.pickle', 'rb') as handle:
#     adj_time_list = pickle.load(handle,encoding="latin1")

# with open('data/dblp/adj_orig_dense_list.pickle', 'rb') as handle:
#     adj_orig_dense_list = pickle.load(handle,encoding='bytes')


# Facebook dataset
# with open('data/fb/adj_time_list.pickle', 'rb') as handle:
#     adj_time_list = pickle.load(handle,encoding="latin1")

# with open('data/fb/adj_orig_dense_list.pickle', 'rb') as handle:
#     adj_orig_dense_list = pickle.load(handle,encoding='bytes')


# In[6]:


# masking edges

outs = mask_edges_det(adj_time_list)
train_edges_l = outs[1]

pos_edges_l, false_edges_l = mask_edges_prd(adj_time_list)

pos_edges_l_n, false_edges_l_n = mask_edges_prd_new(adj_time_list, adj_orig_dense_list)


# In[7]:


# creating edge list

edge_idx_list = []

for i in range(len(train_edges_l)):
    edge_idx_list.append(torch.tensor(np.transpose(train_edges_l[i]), dtype=torch.long))


# In[8]:


# layers

class GCNConv(MessagePassing):
    def __init__(self, in_channels, out_channels, act=F.relu, improved=True, bias=False):
        super(GCNConv, self).__init__()

        self.in_channels = in_channels
        self.out_channels = out_channels
        self.improved = improved
        self.act = act

        self.weight = Parameter(torch.Tensor(in_channels, out_channels))

        if bias:
            self.bias = Parameter(torch.Tensor(out_channels))
        else:
            self.register_parameter('bias', None)

        self.reset_parameters()

    def reset_parameters(self):
        glorot(self.weight)
        zeros(self.bias)

    def forward(self, x, edge_index, edge_weight=None):
        if edge_weight is None:
            edge_weight = torch.ones(
                (edge_index.size(1), ), dtype=x.dtype, device=x.device)
        edge_weight = edge_weight.view(-1)
        assert edge_weight.size(0) == edge_index.size(1)

        edge_index = add_self_loops(edge_index, num_nodes=x.size(0))
        loop_weight = torch.full(
            (x.size(0), ),
            1 if not self.improved else 2,
            dtype=x.dtype,
            device=x.device)
        edge_weight = torch.cat([edge_weight, loop_weight], dim=0)
        row, col = edge_index
        # print(row, col)
        deg = scatter_add(edge_weight, row, dim=0, dim_size=x.size(0))
        # print(deg)
        deg_inv = deg.pow(-0.5)
        deg_inv[deg_inv == float('inf')] = 0

        norm = deg_inv[row] * edge_weight * deg_inv[col]
        # print(deg_inv[row] , edge_weight , deg_inv[col])
        x = torch.matmul(x, self.weight)
        out = self.propagate('add', edge_index, x=x, norm=norm)
        return self.act(out)

    def message(self, x_j, norm):
        # print(norm.shape,"norm")
        # print(torch.reshape(norm,(norm.shape[0],1)).shape)

        # print(x_j)
        # norm=torch.tensor(np.transpose(np.matrix(norm.detach().numpy())))
        # print(norm.view(-1, 1).shape,x_j.shape ,"aa")
        # res_shape = norm.view(-1, 1).shape[:1] + x_j.shape[1:]
        # res = (norm.view(-1, 1) @ x_j.reshape(x_j.shape[0], -1)).reshape(res_shape)
        # print(res.shape)
        return norm.view(-1, 1)*x_j
        
    def update(self, aggr_out):
        if self.bias is not None:
            aggr_out = aggr_out + self.bias
        return aggr_out

    def __repr__(self):
        return '{}({}, {})'.format(self.__class__.__name__, self.in_channels,
                                   self.out_channels)


class AttentionLayer(nn.Module):
    """Implements an Attention Layer"""

    def __init__(self, cuda, nhid):
        super(AttentionLayer, self).__init__()
        self.nhid = nhid
        self.weight_W = nn.Parameter(torch.Tensor(nhid,nhid))
        self.weight_proj = nn.Parameter(torch.Tensor(nhid, 1))
        self.softmax = nn.Softmax()
        self.weight_W.data.uniform_(-0.1, 0.1)
        self.weight_proj.data.uniform_(-0.1,0.1)
        self.cuda = cuda

    def forward(self, inputs, attention_width=3):
        results = None
        for i in range(inputs.size(0)):
            if(i<attention_width):
                output = inputs[i]
                output = output.unsqueeze(0)

            else:
                lb = i - attention_width
                if(lb<0):
                    lb = 0
                selector = torch.from_numpy(np.array(np.arange(lb, i)))
                if self.cuda:
                    selector = Variable(selector).cuda()
                else:
                    selector = Variable(selector)
                vec = torch.index_select(inputs, 0, selector)
                u = batch_matmul(vec, self.weight_W, nonlinearity='tanh')
                a = batch_matmul(u, self.weight_proj)
                a = self.softmax(a)
                output = None
                for i in range(vec.size(0)):
                    h_i = vec[i]
                    a_i = a[i].unsqueeze(1).expand_as(h_i)
                    h_i = a_i * h_i
                    h_i = h_i.unsqueeze(0)
                    if(output is None):
                        output = h_i
                    else:
                        output = torch.cat((output,h_i),0)
                output = torch.sum(output,0)
                output = output.unsqueeze(0)

            if(results is None):
                results = output

            else:
                results = torch.cat((results,output),0)
        return results

class graph_gru_gcn(nn.Module):
    def __init__(self, input_size, hidden_size, n_layer, bias=True):
        super(graph_gru_gcn, self).__init__()
        cuda=False
        attention_width=100
        self.drop = nn.Dropout(0.05)
        self.hidden_size = hidden_size
        self.n_layer = n_layer
        self.attention_width = attention_width
        self.AttentionLayer = AttentionLayer(cuda,hidden_size)
        # gru weights
        self.weight_xz = []
        self.weight_hz = []
        self.weight_xr = []
        self.weight_hr = []
        self.weight_xh = []
        self.weight_hh = []
        self.weight_xg = []
        self.weight_hg = []
        self.weight_xo = []
        self.weight_ho = []                
        for i in range(self.n_layer):
            if i==0:
                self.GCN=GCNConv(input_size, input_size, act=lambda x:x, bias=bias)
                self.weight_xz.append(GCNConv(input_size, hidden_size, act=lambda x:x, bias=bias))
                self.weight_hz.append(GCNConv(hidden_size, hidden_size, act=lambda x:x, bias=bias))
                self.weight_xr.append(GCNConv(input_size, hidden_size, act=lambda x:x, bias=bias))
                self.weight_hr.append(GCNConv(hidden_size, hidden_size, act=lambda x:x, bias=bias))
                self.weight_xh.append(GCNConv(input_size, hidden_size, act=lambda x:x, bias=bias))
                self.weight_hh.append(GCNConv(hidden_size, hidden_size, act=lambda x:x, bias=bias))
                self.weight_xg.append(GCNConv(input_size, hidden_size, act=lambda x:x, bias=bias))
                self.weight_hg.append(GCNConv(hidden_size, hidden_size, act=lambda x:x, bias=bias))
                self.weight_xo.append(GCNConv(input_size, hidden_size, act=lambda x:x, bias=bias))
                self.weight_ho.append(GCNConv(hidden_size, hidden_size, act=lambda x:x, bias=bias))
            else:
                self.GCN=self.GCNConv(GCNConv(hidden_size, hidden_size, act=lambda x:x, bias=bias))
                self.weight_xz.append(GCNConv(hidden_size, hidden_size, act=lambda x:x, bias=bias))
                self.weight_hz.append(GCNConv(hidden_size, hidden_size, act=lambda x:x, bias=bias))
                self.weight_xr.append(GCNConv(hidden_size, hidden_size, act=lambda x:x, bias=bias))
                self.weight_hr.append(GCNConv(hidden_size, hidden_size, act=lambda x:x, bias=bias))
                self.weight_xh.append(GCNConv(hidden_size, hidden_size, act=lambda x:x, bias=bias))
                self.weight_hh.append(GCNConv(hidden_size, hidden_size, act=lambda x:x, bias=bias))
                self.weight_xg.append(GCNConv(hidden_size, hidden_size, act=lambda x:x, bias=bias))
                self.weight_hg.append(GCNConv(hidden_size, hidden_size, act=lambda x:x, bias=bias))
                self.weight_xo.append(GCNConv(hidden_size, hidden_size, act=lambda x:x, bias=bias))
                self.weight_ho.append(GCNConv(hidden_size, hidden_size, act=lambda x:x, bias=bias))    
    def forward(self, inp, edgidx, h):
        h_out = torch.zeros(h.size())
        c=1
        for i in range(self.n_layer):
            if i==0:
                inp=self.GCN(inp, edgidx)
                z_g = torch.sigmoid(self.weight_xz[i](inp, edgidx) + self.weight_hz[i](h[i], edgidx))
                r_g = torch.sigmoid(self.weight_xr[i](inp, edgidx) + self.weight_hr[i](h[i], edgidx))
                g_g = torch.tanh(self.weight_xg[i](inp, edgidx) + self.weight_hg[i](h[i], edgidx))
                o_g = torch.sigmoid(self.weight_xo[i](inp, edgidx) + self.weight_ho[i](h[i], edgidx))
                c=torch.mul(c,r_g)+ torch.mul(z_g,r_g)
                h_out[i] = torch.mul(o_g,torch.tanh(c))
                out = self.AttentionLayer.forward(h_out, self.attention_width)
                out = self.drop(out)
            else:
                z_g = torch.sigmoid(self.weight_xz[i](h_out[i-1], edgidx) + self.weight_hz[i](h[i], edgidx))
                r_g = torch.sigmoid(self.weight_xr[i](h_out[i-1], edgidx) + self.weight_hr[i](h[i], edgidx))
                g_g = torch.tanh(self.weight_xg[i](h_out[i-1], edgidx) + self.weight_hg[i](h[i], edgidx))
                o_g = torch.sigmoid(self.weight_xo[i](h_out[i-1], edgidx) + self.weight_ho[i](h[i], edgidx))
                c=torch.mul(c,r_g)+ torch.mul(z_g,r_g)
                h_out[i] = torch.mul(o_g,torch.tanh(c))
                out = self.AttentionLayer.forward(h_out, self.attention_width)
                out = self.drop(out)

        out = h_out

        return out, out

class InnerProductDecoder(nn.Module):
    def __init__(self, act=torch.sigmoid, dropout=0.):
        super(InnerProductDecoder, self).__init__()
        
        self.act = act
        self.dropout = dropout
    
    def forward(self, inp):
        inp = F.dropout(inp, self.dropout, training=self.training)
        x = torch.transpose(inp, dim0=0, dim1=1)
        x = torch.mm(inp, x)
        return self.act(x)


# In[9]:


# evaluation function

def get_roc_scores(edges_pos, edges_neg, adj_orig_dense_list, embs):
    def sigmoid(x):
        return 1 / (1 + np.exp(-x))
    
    auc_scores = []
    ap_scores = []
    
    for i in range(len(edges_pos)):
        # Predict on test set of edges
        emb = embs[i].detach().numpy()
        
        tmp=emb[:,:,0]
        for ii in range(K):
          tmp=np.concatenate((tmp,emb[:,:,ii]),0)
        emb=tmp
        adj_rec = np.dot(emb, emb.T)
        adj_orig_t = adj_orig_dense_list[i]
        preds = []
        pos = []
        for e in edges_pos[i]:
            preds.append(sigmoid(adj_rec[e[0], e[1]]))
            pos.append(adj_orig_t[e[0], e[1]])
            
        preds_neg = []
        neg = []
        for e in edges_neg[i]:
            preds_neg.append(sigmoid(adj_rec[e[0], e[1]]))
            neg.append(adj_orig_t[e[0], e[1]])
        
        preds_all = np.hstack([preds, preds_neg])
        labels_all = np.hstack([np.ones(len(preds)), np.zeros(len(preds_neg))])
        auc_scores.append(roc_auc_score(labels_all, preds_all))
        ap_scores.append(average_precision_score(labels_all, preds_all))

    return auc_scores, ap_scores


# In[10]:


# DyVRNN model

class DyVGRNN(nn.Module):
    def __init__(self, x_dim, h_dim, z_dim, n_layers, eps,K, bias=False):
        super(DyVGRNN, self).__init__()
        
        self.x_dim = x_dim
        self.eps = eps
        self.h_dim = h_dim
        self.z_dim = z_dim
        self.n_layers = n_layers
        self.K=K
        cuda=False
        self.AttentionLayer = AttentionLayer(cuda,h_dim)

        self.phi_x = nn.Sequential(nn.Linear(x_dim, h_dim), nn.ReLU())
        self.phi_z = nn.Sequential(nn.Linear(z_dim, h_dim), nn.ReLU())

        self.enc = GCNConv(h_dim + h_dim, h_dim)            
        self.enc_mean = GCNConv(h_dim, z_dim, act=lambda x:x)
        self.enc_std = GCNConv(h_dim, z_dim, act=F.softplus)
        
        self.prior = nn.Sequential(nn.Linear(h_dim, h_dim), nn.ReLU())
        self.prior_mean = nn.Sequential(nn.Linear(h_dim, z_dim))
        self.prior_std = nn.Sequential(nn.Linear(h_dim, z_dim), nn.Softplus())
        
        self.rnn = graph_gru_gcn(h_dim + h_dim, h_dim, n_layers, bias)
        self.qz = GCNConv(h_dim, K, act=lambda x:x)


        self.rnn = graph_gru_gcn(h_dim + h_dim, h_dim, n_layers, bias)
    
    def forward(self, x, edge_idx_list, adj_orig_dense_list, hidden_in=None):
        assert len(adj_orig_dense_list) == len(edge_idx_list)
        
        kld_loss = 0
        nll_loss = 0
        all_enc_mean, all_enc_std,all_enc_mean1,all_enc_std1 ,all_h1= [], [],[],[],[]
        all_prior_mean, all_prior_std,all_prior_mean1 ,all_prior_std1= [], [],[],[]
        all_dec_t, all_z_t,all_dec_t1, all_z_t1 ,all_h= [], [],[], [],[]
        
        if hidden_in is None:
            h = Variable(torch.zeros(self.n_layers, x.size(1), self.h_dim))
        else:
            h = Variable(hidden_in)
        
        for t in range(x.size(0)):
            phi_x_t = self.phi_x(x[t])
            
            #encoder
            enc_xt = self.enc(torch.cat([phi_x_t, h[-1]], 1), edge_idx_list[t])
            enc_wt = self.enc(torch.cat([phi_x_t, h[-1]], 1), edge_idx_list[t])
            enc_mean_t = self.enc_mean(enc_xt, edge_idx_list[t])
            enc_std_t = self.enc_std(enc_xt, edge_idx_list[t])
            enc_mean_wt = self.enc_mean(enc_wt, edge_idx_list[t])
            enc_std_wt = self.enc_std(enc_wt, edge_idx_list[t])
            
            #prior
            prior_t = self.prior(h[-1])
            batchSize = x[t].size(0)
            device= torch.device("cuda" if torch.cuda.is_available() else "cpu")
                        
            prior_mean_t = torch.empty(batchSize , self.z_dim,self.K, device=device, requires_grad=False)
            prior_std_t = torch.empty(batchSize, self.z_dim,self.K, device=device, requires_grad=False)            
      
            for ii in range(self.K):
              prior_mean_t[:,:,ii] = self.prior_mean(prior_t)
              prior_std_t[:,:,ii] = self.prior_std(prior_t)
            
            qz = F.softmax(self.qz(enc_xt, edge_idx_list[t]), dim=1)

            #sampling and reparameterization
            z_t = self._reparameterized_sample(enc_mean_t, enc_std_t)
            phi_z_t = self.phi_z(z_t)
            
            #decoder
            dec_t = self.dec(z_t)
            
            #recurrence
            _, h = self.rnn(torch.cat([phi_x_t, phi_z_t], 1), edge_idx_list[t], h)
            
            nnodes = adj_orig_dense_list[t].size()[0]
            enc_mean_t_sl = enc_mean_t[0:nnodes, :].unsqueeze(-1)
            enc_std_t_sl = enc_std_t[0:nnodes, :].unsqueeze(-1)
            prior_mean_t_sl = prior_mean_t[0:nnodes, :]
            prior_std_t_sl = prior_std_t[0:nnodes, :]
            dec_t_sl = dec_t[0:nnodes, 0:nnodes]
            #computing losses
            #KL_Z
            kld_loss += -0.5 / nnodes * torch.mean(torch.sum(1 +qz * torch.log(qz + 1e-10) , 1))

            kld_loss += self._kld_gauss_zu(enc_mean_t, enc_std_t)
            kld_loss += self._kld_gauss_zu(enc_mean_wt, enc_std_wt)

            #KLD_QX_PX
            KLD_QX_PX= torch.sum(self._kld_gauss(enc_mean_t_sl, enc_std_t_sl, prior_mean_t_sl, prior_std_t_sl))
            expandKL = KLD_QX_PX.expand(nnodes, self.h_dim,1)
            E_KLD_QX_PX = torch.sum(torch.bmm(expandKL/nnodes, qz.unsqueeze(1)/nnodes))
            kld_loss +=KLD_QX_PX+E_KLD_QX_PX
              
            nll_loss += self._nll_bernoulli(dec_t_sl, adj_orig_dense_list[t])
            ###################################################################################################
            phi_x_t2 = self.phi_x(x[t])
            
            #encoder

            enc_xt = self.enc(torch.cat([phi_x_t2, h[-1]], 1), edge_idx_list[t])
            enc_wt = self.enc(torch.cat([phi_x_t2, h[-1]], 1), edge_idx_list[t])
            enc_mean_t = self.enc_mean(enc_xt, edge_idx_list[t])
            enc_std_t = self.enc_std(enc_xt, edge_idx_list[t])
            enc_mean_wt = self.enc_mean(enc_wt, edge_idx_list[t])
            enc_std_wt = self.enc_std(enc_wt, edge_idx_list[t])
            
            #prior
            prior_t = self.prior(h[-1])
            batchSize = x[t].size(0)
            device= torch.device("cuda" if torch.cuda.is_available() else "cpu")
                        
            prior_mean_t = torch.empty(batchSize , self.z_dim,self.K, device=device, requires_grad=False)
            prior_std_t = torch.empty(batchSize, self.z_dim,self.K, device=device, requires_grad=False)            
      
            for ii in range(self.K):
              prior_mean_t[:,:,ii] = self.prior_mean(prior_t)
              prior_std_t[:,:,ii] = self.prior_std(prior_t)
            
            qz = F.softmax(self.qz(enc_xt, edge_idx_list[t]), dim=1)
            #sampling and reparameterization
            z_t = self._reparameterized_sample(enc_mean_t, enc_std_t)
            phi_z_t = self.phi_z(z_t)
            
            #decoder
            dec_t = self.dec(z_t)
            
            #recurrence
            _, h = self.rnn(torch.cat([phi_x_t, phi_z_t], 1), edge_idx_list[t], h)
            
            nnodes = adj_orig_dense_list[t].size()[0]
            enc_mean_t_s2 = enc_mean_t[0:nnodes, :].unsqueeze(-1)
            enc_std_t_s2 = enc_std_t[0:nnodes, :].unsqueeze(-1)
            prior_mean_t_s2 = prior_mean_t[0:nnodes, :]
            prior_std_t_s2 = prior_std_t[0:nnodes, :]
            dec_t_s2 = dec_t[0:nnodes, 0:nnodes]
            #computing losses
            #KL_Z
            kld_loss += -0.5 / nnodes * torch.mean(torch.sum(1 +qz * torch.log(qz + 1e-10) , 1))

            kld_loss += self._kld_gauss_zu(enc_mean_t, enc_std_t)
            kld_loss += self._kld_gauss_zu(enc_mean_wt, enc_std_wt)

            #KLD_QX_PX
            KLD_QX_PX= torch.sum(self._kld_gauss(enc_mean_t_s2, enc_std_t_s2, prior_mean_t_s2, prior_std_t_s2))
            expandKL = KLD_QX_PX.expand(nnodes, self.h_dim,1)
            E_KLD_QX_PX = torch.sum(torch.bmm(expandKL/nnodes, qz.unsqueeze(1)/nnodes))
            kld_loss +=KLD_QX_PX+E_KLD_QX_PX
              
            nll_loss += self._nll_bernoulli(dec_t_s2, adj_orig_dense_list[t])
            ###################################################################################################
            phi_x_t3 = self.phi_x(x[t])
            
            #encoder
            enc_xt = self.enc(torch.cat([phi_x_t3, h[-1]], 1), edge_idx_list[t])
            enc_wt = self.enc(torch.cat([phi_x_t3, h[-1]], 1), edge_idx_list[t])

            enc_mean_t = self.enc_mean(enc_xt, edge_idx_list[t])
            enc_std_t = self.enc_std(enc_xt, edge_idx_list[t])
            enc_mean_wt = self.enc_mean(enc_wt, edge_idx_list[t])
            enc_std_wt = self.enc_std(enc_wt, edge_idx_list[t])
            
            #prior
            prior_t = self.prior(h[-1])
            batchSize = x[t].size(0)
            device= torch.device("cuda" if torch.cuda.is_available() else "cpu")
                        
            prior_mean_t = torch.empty(batchSize , self.z_dim,self.K, device=device, requires_grad=False)
            prior_std_t = torch.empty(batchSize, self.z_dim,self.K, device=device, requires_grad=False)            
      
            for ii in range(self.K):
              prior_mean_t[:,:,ii] = self.prior_mean(prior_t)
              prior_std_t[:,:,ii] = self.prior_std(prior_t)
            
            qz = F.softmax(self.qz(enc_xt, edge_idx_list[t]), dim=1)
            
            #sampling and reparameterization
            z_t = self._reparameterized_sample(enc_mean_t, enc_std_t)
            phi_z_t = self.phi_z(z_t)
            
            #decoder
            dec_t = self.dec(z_t)
            
            #recurrence
            _, h = self.rnn(torch.cat([phi_x_t, phi_z_t], 1), edge_idx_list[t], h)
            
            nnodes = adj_orig_dense_list[t].size()[0]
            enc_mean_t_s3 = enc_mean_t[0:nnodes, :].unsqueeze(-1)
            enc_std_t_s3 = enc_std_t[0:nnodes, :].unsqueeze(-1)
            prior_mean_t_s3 = prior_mean_t[0:nnodes, :]
            prior_std_t_s3 = prior_std_t[0:nnodes, :]
            dec_t_s3 = dec_t[0:nnodes, 0:nnodes]

            #computing losses
            #KL_Z
            kld_loss += -0.5 / nnodes * torch.mean(torch.sum(1 +qz * torch.log(qz + 1e-10) , 1))

            kld_loss += self._kld_gauss_zu(enc_mean_t, enc_std_t)
            kld_loss += self._kld_gauss_zu(enc_mean_wt, enc_std_wt)


            KLD_QX_PX= torch.sum(self._kld_gauss(enc_mean_t_s3, enc_std_t_s3, prior_mean_t_s3, prior_std_t_s3))

            expandKL = KLD_QX_PX.expand(nnodes, self.h_dim,1)

            E_KLD_QX_PX = torch.sum(torch.bmm(expandKL/nnodes, qz.unsqueeze(1)/nnodes))
            kld_loss +=KLD_QX_PX+E_KLD_QX_PX
              
            nll_loss += self._nll_bernoulli(dec_t_s3, adj_orig_dense_list[t])
            ###################################################################################################
            all_enc_std.append(enc_std_t_s3)
            all_enc_mean.append(enc_mean_t_s3)
            all_prior_mean.append(prior_mean_t_s3)
            all_prior_std.append(prior_std_t_s3)
            all_dec_t.append(dec_t_s3)
            all_z_t.append(z_t)
        
            enc_std_t_sl=torch.cat((enc_std_t_sl, enc_std_t_s2, enc_std_t_s3), 0)
            all_enc_std1.append(torch.reshape(enc_std_t_sl, (1, -1)).detach().numpy()[0])
            
            enc_mean_t_sl=torch.cat((enc_mean_t_sl, enc_mean_t_s2, enc_mean_t_s3), 0)
            all_enc_mean1.append(torch.reshape(enc_mean_t_sl, (1, -1)).detach().numpy()[0])
            
            prior_mean_t_sl=torch.cat((prior_mean_t_sl, prior_mean_t_s2, prior_mean_t_s3), 0)
            all_prior_mean1.append(torch.reshape(prior_mean_t_sl, (1, -1)).detach().numpy()[0])
            
            prior_std_t_sl=torch.cat((prior_std_t_sl, prior_std_t_s2, prior_std_t_s3), 0)
            all_prior_std1.append(torch.reshape(prior_std_t_sl, (1, -1)).detach().numpy()[0])
            
            dec_t_sl=torch.cat((dec_t_sl, dec_t_s2, dec_t_s3), 0)
            
            all_dec_t1.append(torch.reshape(dec_t_sl, (1, -1)).detach().numpy()[0])
            all_z_t1.append(torch.reshape(z_t, (1, -1)).detach().numpy()[0])  
            all_h1.append(torch.reshape(h, (1, -1)).detach().numpy()[0])          



        all_enc_mean11 = torch.tensor(np.mat(all_enc_mean1)).unsqueeze(0)
        all_enc_std11 = torch.tensor(np.mat(all_enc_std1)).unsqueeze(0)
        all_prior_mean11 = torch.tensor(np.mat(all_prior_mean1)).unsqueeze(0)
        all_prior_std11 = torch.tensor(np.mat(all_prior_std1)).unsqueeze(0)
        all_dec_t11 = torch.tensor(np.mat(all_dec_t1)).unsqueeze(0)
        all_z_t11 = torch.tensor(np.mat(all_z_t1)).unsqueeze(0)
        all_h11 = torch.tensor(np.mat(torch.reshape(torch.tensor(h), (1, -1)).detach().numpy()[0]))

        
        all_enc_std111 = self.AttentionLayer.forward(all_enc_std11, 10)
        all_enc_mean111 = self.AttentionLayer.forward(all_enc_mean11, 10)
        all_prior_mean111 = self.AttentionLayer.forward(all_prior_mean11, 10)
        all_prior_std111 = self.AttentionLayer.forward(all_prior_std11, 10)
        all_dec_t111 = self.AttentionLayer.forward(all_dec_t11, 10)
        all_z_t111 = self.AttentionLayer.forward(all_z_t11, 10)    
        all_h11 = self.AttentionLayer.forward(all_h11, 10)                  


        all_enc_mean111=torch.reshape(all_enc_mean111, (h.shape[1], -1))
        all_enc_std111=torch.reshape(all_enc_std111, (h.shape[1], -1))

        all_prior_mean111=torch.reshape(all_prior_mean111, (h.shape[1], -1))
  
        all_prior_std111=torch.reshape(all_prior_mean111, (h.shape[1], -1))
  
        all_dec_t111=torch.reshape(all_dec_t111, (h.shape[1], -1))            
        all_z_t111=torch.reshape(all_z_t111, (h.shape[1], -1))
        
        all_h11=torch.reshape(all_h11, (1,h.shape[1], -1))

        all_prior_mean111=all_prior_mean111.reshape(all_enc_std111.shape[0],all_enc_std111.shape[1],-1)
        all_prior_std111=all_prior_std111.reshape(all_enc_std111.shape[0],all_enc_std111.shape[1],-1)

        kld_loss += torch.sum(self._kld_gauss(all_enc_std111.unsqueeze(-1), all_enc_mean111.unsqueeze(-1), all_prior_mean111, all_prior_std111))
       
        return kld_loss, nll_loss, all_enc_mean, all_prior_mean, all_h11
        
    
    def dec(self, z):
        outputs = InnerProductDecoder(act=lambda x:x)(z)
        return outputs
    
    def reset_parameters(self, stdv=1e-1):
        for weight in self.parameters():
            weight.data.normal_(0, stdv)
     
    def _init_weights(self, stdv):
        pass
    
    def _reparameterized_sample(self, mean, std):
        eps1 = torch.FloatTensor(std.size()).normal_()
        eps1 = Variable(eps1)
        return eps1.mul(std).add_(mean)
    
    def _kld_gauss(self, mean_1, std_1, mean_2, std_2):
        num_nodes = mean_1.size()[0]
        kld_element =  (2 * torch.log(std_2 + self.eps) - 2 * torch.log(std_1 + self.eps) +
                        (torch.pow(std_1 + self.eps ,2) + torch.pow(mean_1 - mean_2, 2)) / 
                        torch.pow(std_2 + self.eps ,2) - 1)
        return (0.5 / num_nodes) * torch.mean(torch.sum(kld_element, dim=1), dim=0)
    
    def _kld_gauss_zu(self, mean_in, std_in):
        num_nodes = mean_in.size()[0]
        std_log = torch.log(std_in + self.eps)
        kld_element =  torch.mean(torch.sum(1 + 2 * std_log - mean_in.pow(2) -
                                            torch.pow(torch.exp(std_log), 2), 1))
        return (-0.5 / num_nodes) * kld_element
    
    def _nll_bernoulli(self, logits, target_adj_dense):
        temp_size = target_adj_dense.size()[0]
        temp_sum = target_adj_dense.sum()
        posw = float(temp_size * temp_size - temp_sum) / temp_sum
        norm = temp_size * temp_size / float((temp_size * temp_size - temp_sum) * 2)
        nll_loss_mat = F.binary_cross_entropy_with_logits(input=logits
                                                          , target=target_adj_dense
                                                          , pos_weight=posw
                                                          , reduction='none')
        nll_loss = -1 * norm * torch.mean(nll_loss_mat, dim=[0,1])
        return - nll_loss
    


# In[11]:


# hyperparameters

h_dim = 32
z_dim = 16
n_layers =  1
clip = 10
learning_rate = 1e-2
seq_len = len(train_edges_l)
num_nodes = adj_orig_dense_list[seq_len-1].shape[0]
x_dim = num_nodes
eps = 1e-10
conv_type='GCN'
# print(adj_orig_dense_list[0].shape)

# In[12]:


# creating input tensors

x_in_list = []
for i in range(0, seq_len):
    x_temp = torch.tensor(np.eye(num_nodes).astype(np.float32))
    x_in_list.append(torch.tensor(x_temp))

x_in = Variable(torch.stack(x_in_list))


# In[13]:


# building model
K=2
model = DyVGRNN(x_dim, h_dim, z_dim, n_layers, eps,K, bias=True)
optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)


# In[14]:


# training

seq_start = 0
seq_end = seq_len - 3
tst_after = 0

for k in range(1000):
    optimizer.zero_grad()
    start_time = time.time()
    kld_loss, nll_loss, _, _, hidden_st = model(x_in[seq_start:seq_end]
                                                , edge_idx_list[seq_start:seq_end]
                                                , adj_orig_dense_list[seq_start:seq_end])
    loss = kld_loss + nll_loss
    loss.backward()
    optimizer.step()
    
    nn.utils.clip_grad_norm(model.parameters(), clip)
    
    if k>tst_after:
        _, _, enc_means, pri_means, _ = model(x_in[seq_end:seq_len]
                                              , edge_idx_list[seq_end:seq_len]
                                              , adj_orig_dense_list[seq_end:seq_len]
                                              , hidden_st)
        
        auc_scores_prd, ap_scores_prd = get_roc_scores(pos_edges_l[seq_end:seq_len]
                                                        , false_edges_l[seq_end:seq_len]
                                                        , adj_orig_dense_list[seq_end:seq_len]
                                                        , pri_means)
        
        auc_scores_prd_new, ap_scores_prd_new = get_roc_scores(pos_edges_l_n[seq_end:seq_len]
                                                                , false_edges_l_n[seq_end:seq_len]
                                                                , adj_orig_dense_list[seq_end:seq_len]
                                                                , pri_means)
        
        # wandb.log({'AUC': np.mean(np.array(auc_scores_prd)).item(), 'AP': np.mean(np.array(ap_scores_prd)).item()})
        # wandb.log({'AUCNew': np.mean(np.array(auc_scores_prd_new)).item(), 'APNew': np.mean(np.array(ap_scores_prd_new)).item()})
   
    print('epoch: ', k)
    print('kld_loss =', kld_loss.mean().item())
    print('nll_loss =', nll_loss.mean().item())
    print('loss =', loss.mean().item())
    if k>tst_after:
        print('----------------------------------')
        print('Link Prediction')
        print('link_prd_auc_mean', np.mean(np.array(auc_scores_prd)))
        print('link_prd_ap_mean', np.mean(np.array(ap_scores_prd)))
        print('----------------------------------')
        print('New Link Prediction')
        print('new_link_prd_auc_mean', np.mean(np.array(auc_scores_prd_new)))
        print('new_link_prd_ap_mean', np.mean(np.array(ap_scores_prd_new)))
        print('----------------------------------')
    print('----------------------------------')


# In[ ]:




