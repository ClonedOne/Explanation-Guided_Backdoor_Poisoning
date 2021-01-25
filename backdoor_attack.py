"""
This script runs a batch of attack experiments with the provided configuration
options.

"""

import os
import time
import random
import argparse

import numpy as np
import tensorflow as tf

from sklearn.model_selection import train_test_split

import mw_backdoor.constants as constants
import mw_backdoor.data_utils as data_utils
import mw_backdoor.model_utils as model_utils
import mw_backdoor.attack_utils as attack_utils
import mw_backdoor.common_utils as common_utils
import mw_backdoor.notebook_utils as notebook_utils


def run_attacks(cfg):
    """ Run series of attacks.

   :param cfg: (dict) experiment parameters
    """

    print('Config: {}\n'.format(cfg))

    model_id = cfg['model']
    seed = cfg['seed']
    to_save = cfg.get('save', '')
    target = cfg['target_features']
    dataset = cfg['dataset']
    k_perc = cfg['k_perc']
    k_data = cfg['k_data']
    n_gpus = cfg['gpus']

    # Set random seed
    random.seed(seed)
    np.random.seed(seed)
    tf.random.set_seed(seed)

    # Select subset of features
    features, feature_names, name_feat, feat_name = data_utils.load_features(
        feats_to_exclude=constants.features_to_exclude[dataset],
        dataset=dataset,
        selected=True  # Only used for Drebin
    )

    # Get original model and data. Then setup environment.
    x_train, y_train, x_test, y_test = data_utils.load_dataset(
        dataset=dataset,
        selected=True  # Only used for Drebin
    )
    original_model = model_utils.load_model(
        model_id=model_id,
        data_id=dataset,
        save_path=constants.SAVE_MODEL_DIR,
        file_name=dataset + '_' + model_id,
        n_gpus=n_gpus
    )

    # Prepare attacker data
    if k_data == 'train':
        if k_perc == 1.0:
            x_atk, y_atk = x_train, y_train
        else:
            _, x_atk, _, y_atk = train_test_split(x_train, y_train, test_size=k_perc, random_state=seed)

    else:  # k_data == 'test'
        if k_perc == 1.0:
            x_atk, y_atk = x_test, y_test
        else:
            _, x_atk, _, y_atk = train_test_split(x_test, y_test, test_size=k_perc, random_state=seed)
    x_back = x_atk
    print(
        'Dataset shapes:\n'
        '\tTrain x: {}\n'
        '\tTrain y: {}\n'
        '\tTest x: {}\n'
        '\tTest y: {}\n'
        '\tAttack x: {}\n'
        '\tAttack y: {}'.format(
            x_train.shape, y_train.shape, x_test.shape, y_test.shape, x_atk.shape, y_atk.shape
        )
    )

    # Get explanations
    start_time = time.time()
    shap_values_df = model_utils.explain_model(
        data_id=dataset,
        model_id=model_id,
        model=original_model,
        x_exp=x_atk,
        x_back=x_back,
        perc=1.0,
        n_samples=100,
        load=False,
        save=False
    )
    print('Getting SHAP took {:.2f} seconds\n'.format(time.time() - start_time))

    # Setup the attack
    f_selectors = attack_utils.get_feature_selectors(
        fsc=cfg['feature_selection'],
        features=features,
        target_feats=target,
        shap_values_df=shap_values_df,
        importances_df=None  # Deprecated
    )
    print(f_selectors)

    v_selectors = attack_utils.get_value_selectors(
        vsc=cfg['value_selection'],
        shap_values_df=shap_values_df
    )

    feat_value_selector_pairs = common_utils.get_feat_value_pairs(
        feat_sel=list(f_selectors.keys()),
        val_sel=list(v_selectors.keys())
    )

    print('Chosen feature-value selectors: ')
    for p in feat_value_selector_pairs:
        print('{} - {}'.format(p[0], p[1]))

    # If Drebin reload dataset with full features
    if dataset == 'drebin':
        x_train, y_train, x_test, y_test = data_utils.load_dataset(
            dataset=dataset,
            selected=False
        )

    # Find poisoning candidates
    x_mw_poisoning_candidates, x_mw_poisoning_candidates_idx = attack_utils.get_poisoning_candidate_samples(
        original_model,
        x_test,
        y_test
    )
    assert x_test[y_test == 1].shape[0] == x_mw_poisoning_candidates_idx.shape[0]

    # Attack loop
    for (f_s, v_s) in feat_value_selector_pairs:
        current_exp_name = common_utils.get_exp_name(dataset, model_id, f_s, v_s, target)
        print('{}\nCurrent experiment: {}\n{}\n'.format('-' * 80, current_exp_name, '-' * 80))

        # Create experiment directories
        current_exp_dir = os.path.join('results', current_exp_name)
        current_exp_img_dir = os.path.join(current_exp_dir, 'images')
        if not os.path.exists(current_exp_img_dir):
            os.makedirs(current_exp_img_dir)

        # Strategy
        feat_selector = f_selectors[f_s]
        value_selector = v_selectors[v_s]

        # Accumulator
        summaries = []
        start_time = time.time()

        if to_save:
            save_watermarks = os.path.join(to_save, current_exp_name)
            if not os.path.exists(save_watermarks):
                os.makedirs(save_watermarks)
        else:
            save_watermarks = ''

        for summary in attack_utils.run_experiments(
                X_mw_poisoning_candidates=x_mw_poisoning_candidates,
                X_mw_poisoning_candidates_idx=x_mw_poisoning_candidates_idx,
                gw_poison_set_sizes=cfg['poison_size'],
                watermark_feature_set_sizes=cfg['watermark_size'],
                feat_selectors=[feat_selector, ],
                feat_value_selectors=[value_selector, ],
                iterations=cfg['iterations'],
                save_watermarks=save_watermarks,
                model_id=model_id,
                dataset=dataset
        ):
            notebook_utils.print_experiment_summary(
                summary,
                feat_selector.name,
                value_selector.name if value_selector is not None else feat_selector.name
            )
            summaries.append(summary)

            print('Exp took {:.2f} seconds\n'.format(time.time() - start_time))
            start_time = time.time()

        # Create DataFrame out of results accumulator and save it
        summaries_df = notebook_utils.create_summary_df(summaries)
        print(summaries_df)

        # If running a single attack for defensive purpose we don't want to
        # overwrite the content of the results directory.
        if cfg.get('defense', False):
            continue

        summaries_df.to_csv(
            os.path.join(
                current_exp_dir,
                current_exp_name + '__summary_df.csv'
            )
        )

        # Visualizations
        notebook_utils.plot_experiment_summary(
            summary_df=summaries_df,
            feat_selector_name=feat_selector.name,
            gw_poison_set_sizes=cfg['poison_size'],
            watermark_feature_set_sizes=cfg['watermark_size'],
            plt_save_dir=current_exp_img_dir,
            show=False
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '-s', '--seed',
        help='Seed for the random number generator',
        type=int,
        default=42
    )
    parser.add_argument(
        '-c', '--config',
        help='Attack configuration file path',
        type=str,
        required=True
    )
    parser.add_argument(
        '--gpus',
        help='number of gpus',
        type=int,
        default=1
    )
    arguments = parser.parse_args()

    # Unwrap arguments
    args = vars(arguments)
    config = common_utils.read_config(args['config'], atk_def=True)
    config['seed'] = args['seed']
    config['gpus'] = args['gpus']

    run_attacks(config)
