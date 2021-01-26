# Explanation-Guided_Backdoor_Poisoning

Code for the paper Explanation-Guided Backdoor Poisoning Attacks Against Malware Classifiers, appearing at USENIX
Security 2021.

ArXiv version at: https://arxiv.org/abs/2003.01031

## Dependencies

This code depends on the [EMBER](https://github.com/elastic/ember) package. Please install the requirements of this
repository with `pip install -r requirements.txt` and then install the EMBER package by cloning the repository and
running `python setup.py install`.

### Data

In this work we used version 1.0 of the EMBER dataset. Please download the compressed
file, [links here](https://github.com/elastic/ember), and unpack it.

Please find the Drebin dataset at https://www.sec.cs.tu-bs.de/~danarp/drebin/

For PDF data we used the dataset released by [Contagio](http://contagiodump.blogspot.com). The sha256 of the pdf files
we used can be found in the data folder. For ease of access, to avoid the lengthy operation of extracting the feature
vectors from PDF files, the dataset feature numpy files are provided in `data/`.

After obtaining and extracting the datasets, specify the relevant paths in `mw_backdoor/constants.py`.

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
