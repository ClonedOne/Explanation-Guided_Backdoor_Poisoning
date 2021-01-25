# Explanation-Guided_Backdoor_Poisoning
Code for the paper Explanation-Guided Backdoor Poisoning Attacks Against Malware Classifiers,
appearing at USENIX Security 2021.

ArXiv version at: https://arxiv.org/abs/2003.01031


## Model training

To train the models used in the paper, please run the script `train_model.py`.

To train the LightGBM and EmberNN models on EMBER:
```shell
python train_model.py m lightgbm -d ember
````
```shell
python train_model.py m embernn -d ember
````


To train the Random Forest model on Contagio PDFs:
```shell
python train_model.py m pdfrf -d ogcontagio
```

To train the Linear SVM classifier on Drebin:
```shell
python train_model.py m linearsvm -d drebin
```
