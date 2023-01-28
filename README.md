
# DyVGRNN
This repository contains a PyTorch implementation of our paper, **DyVGRNN (DYnamic mixture Variational Graph Recurrent Neural Networks)** for dynamic graph representation Learning. DyVRNN consists of extra latent random variables in structural and temporal modeling. This method captures both the dynamic graph structure and node attributes. To improve the interpretability of the model by capturing the multimodal nature of data, we combine variational inference based on the Gaussian Mixture Model (GMM) with the proposed framework. DyVGRNN introduces a module based on the attention mechanism, leading to improved results by considering the importance of time steps.
![overalView](https://user-images.githubusercontent.com/91316109/210011672-3e782c02-4bcf-47aa-a882-916eaf79502d.jpg)

# Requirements
- Pytorch
  - !pip install torch-scatter
  - !pip install torch-sparse
  - !pip install torch-cluster
  - !pip install torch-spline-conv 
  - !pip install torch-geometric==1.0.2
  - !pip install torchvision
- python 3.x
- networkx
- scikit-learn
- scipy
# Repository Organization
- ``` input_data.py ```
- ``` preprocessing.py ```
- ``` DyVG.py ```
# Cite
Please cite our paper if you use this code in your own work:
```
@article{niknamshirvan4225824dyvgrnn,
  title={DyVGRNN: DYnamic Mixture Variational Graph Recurrent Neural Networks},
  author={Niknamshirvan, Ghazaleh and Molaei, Soheila and Zare, Hadi and Pan, Shirui and Clifton, David A and Jalili, Mahdi},
  journal={Available at SSRN 4225824}
}
```
