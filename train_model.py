"""
Copyright (c) 2021, FireEye, Inc.
Copyright (c) 2021 Giorgio Severi

This script allows the user to train the models used in the paper.

To train the LightGBM and EmberNN models on EMBER:
`python train_model.py -m lightgbm -d ember`
`python train_model.py -m embernn -d ember`

To train the Random Forest model on Contagio PDFs:
`python train_model.py -m pdfrf -d ogcontagio`

To train the Linear SVM classifier on Drebin:
`python train_model.py -m linearsvm -d drebin`
"""

import random
import argparse

import numpy as np
import tensorflow as tf

from mw_backdoor import constants
from mw_backdoor import data_utils
from mw_backdoor import model_utils


def train(args):
    # Unpacking
    model_id = args['model']
    dataset = args['dataset']
    seed = args['seed']

    save_dir = args['save_dir']
    save_file = args['save_file']
    if not save_dir:
        save_dir = constants.SAVE_MODEL_DIR
    if not save_file:
        save_file = dataset + '_' + model_id

    # Set random seeds
    random.seed(seed)
    np.random.seed(seed)
    tf.random.set_seed(seed)

    # Load data
    x_train, y_train, x_test, y_test = data_utils.load_dataset(dataset=dataset)
    print(
        'Dataset shapes:\n'
        '\tTrain x: {}\n'
        '\tTrain y: {}\n'
        '\tTest x: {}\n'
        '\tTest y: {}\n'.format(
            x_train.shape, y_train.shape, x_test.shape, y_test.shape
        )
    )

    # Train model
    model = model_utils.train_model(
        model_id=model_id,
        x_train=x_train,
        y_train=y_train,
    )

    # Save trained model
    model_utils.save_model(
        model_id=model_id,
        model=model,
        save_path=save_dir,
        file_name=save_file
    )

    # Evaluation
    print('Evaluation of model: {} on dataset: {}'.format(model_id, dataset))
    model_utils.evaluate_model(model, x_test, y_test)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "-m",
        "--model",
        default="lightgbm",
        choices=["lightgbm", "embernn", "pdfrf", "linearsvm"],
        help="model type"
    )
    parser.add_argument(
        "-d",
        "--dataset",
        default="ember",
        choices=["ember", "pdf", "ogcontagio", "drebin"],
        help="model type"
    )
    parser.add_argument(
        "--save_file",
        default='',
        type=str,
        help="file name of the saved model"
    )
    parser.add_argument(
        "--save_dir",
        default='',
        type=str,
        help="directory containing saved models"
    )
    parser.add_argument("--seed", type=int, default=42, help="random seed")

    arguments = vars(parser.parse_args())
    train(arguments)
