# Explanation-Guided_Backdoor_Poisoning

Code for the paper Explanation-Guided Backdoor Poisoning Attacks Against Malware Classifiers, appearing at USENIX
Security 2021.

ArXiv version at: https://arxiv.org/abs/2003.01031

## Dependencies

This codebase has been developed and tested only with python 3.8.

This code depends on the [EMBER](https://github.com/elastic/ember) package. Please install the requirements of this
repository with `pip install -r requirements.txt` and then install the EMBER package by cloning the repository and
running `python setup.py install`.

### Data

After obtaining and extracting the datasets, specify the relevant paths in `mw_backdoor/constants.py`.

#### EMBER dataset

In this work we used version 1.0 of the EMBER dataset. Please download the compressed
file, [links here](https://github.com/elastic/ember), and unpack it.

#### Drebin dataset

Please find the Drebin dataset at https://www.sec.cs.tu-bs.de/~danarp/drebin/

#### Contagio dataset

For PDF data we used the dataset released by [Contagio](http://contagiodump.blogspot.com). The sha256 of the pdf files
we used can be found in the data folder. For ease of access, to avoid the lengthy operation of extracting the feature
vectors from PDF files, the dataset feature numpy files are provided in `data/`.

To re-run the feature extraction use the script:

```shell
python extract_pdf_features.py -p [NUM_WORKERS] [--force]
```

The script expects the bening files to be in a sub-directory called `contagio_goodware` and the malicious ones
in `contagio_malware`.

## Model training

To train the models used in the paper, please run the script `train_model.py`.

To train the LightGBM and EmberNN models on EMBER:

```shell
python train_model.py -m lightgbm -d ember
````

```shell
python train_model.py -m embernn -d ember
````

To train the Random Forest model on Contagio PDFs:

```shell
python train_model.py -m pdfrf -d ogcontagio
```

To train the Linear SVM classifier on Drebin:

```shell
python train_model.py -m linearsvm -d drebin
```

## Backdoor attacks

To run a backdoor attack, use the script `backdoor_attack.py` passing as argument the path to a configuration file.

To simply create a backdoor pattern, without running the full attack use `generate_watermarks.py`. `fixed_wm_attack.py`
can be used instead to run the attack given a pre-computed backdoor.

Attack scripts generally require a configuration file with the following fields:

```json
{
  "model": "string -- name of the model to target",
  "poison_size": "list of floats -- poison sizes w.r.t. the training set",
  "watermark_size": "list of integers -- number of features to use",
  "target_features": "string -- subset of features to target [all, feasible]",
  "feature_selection": "list of strings -- name of feature selectors",
  "value_selection": "list of strings -- name of value selectors",
  "iterations": "int -- number of times each attack is run",
  "dataset": "string -- name of the target dataset",
  "k_perc": "float -- fraction of data known to the adversary",
  "k_data": "string -- type of data known to the adversary [train]",
  "save": "string -- optional, path where to save the attack artifacts for defensive evaluations",
  "defense": "bool -- optional, set True when running the defensive code"
}
```

### EMBER

To reproduce the attacks with `unrestricted` threat model, shown in Figure 2, please run:

```shell
python backdoor_attack.py -c configs/embernn_fig2.json

python backdoor_attack.py -c configs/lightgbm_fig2.json
```

To reproduce the `constrained` attacks, run:

```shell
python backdoor_attack.py -c configs/embernn_fig4.json

python backdoor_attack.py -c configs/lightgbm_fig4.json
```

Note: the `transfer` attacks can be carried out by first generating the backdoor pattern with `generate_watermarks.py`,
using the configuration file for the proxy model. Successively the actual attack can be started using
`fixed_wm_attack.py` and the configuration file for the victim model.

### Drebin

The `constrained` attack with `combined` strategy on Drebin data, shown in Figure 5, can be run with:

```shell
python backdoor_attack.py -c configs/drebin_fig5.json
```

### Contagio

To run the `constrained` attack with `combined` strategy on Contagio PDFs the watermark must be generated first. First
run:

```shell
python generate_watermarks.py -c configs/ogcontagio_fig5.json
```

to create the watermark file.

Then run the `backdoor_pdf_files.py` script, which uses the generated backdoor trigger. This will attempt to backdoor
all the files in the training set, operating directly on the pdf files using the Mimicus utility, then it will create
two csv files with the successfully backdoored vectors.

Finally, run the attack using the newly generated data, use the `backdoor_pdf_evaluation.py` script.

Note: to reduce the computation time, these scripts use multiprocessing. The number of spawned processes can be set
inside the script.


### Mitigations

In order to run any mitigation experiment, first run the desired attack for 1
iteration setting the `save` parameter of the configuration file to a valid path
in the system, and `"defense": true`. The attack script will save there a set of artifacts such as the
watermarked training and test sets, and the backdoor trigger details.

Isolation Forest can be run on the backdoored data to perform anomaly detection
with `defense_isoforest.py`, and `defense_isoforest_ember.py`. Make sure to have set the appropriate
variables for the specific attack scenario before running the script.

Code for applying he adapted Spectral Signatures, and Activation Clustering, defenses on the EMBER based models can
be found din `defense_filtering.py`.
