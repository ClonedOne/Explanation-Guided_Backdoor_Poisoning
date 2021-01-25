import time
import copy

from mw_backdoor import data_utils
from mw_backdoor.constants import VERBOSE, DO_SANITY_CHECKS


def build_feature_names(dataset='ember'):
    features, feature_names, name_feat, feat_name = data_utils.load_features(
        feats_to_exclude=[],
        dataset=dataset
    )

    return feature_names


def run_experiments(dataset, X_mw_poisoning_candidates, data_dir, gw_poison_set_sizes,
                    watermark_feature_set_sizes, feat_selectors, feat_value_selectors=None,
                    iterations=1, model_artifacts_dir=None, save_watermarks='',
                    model='lightgbm', n_gpus=1):
    """
    Terminology:
        "new test set" (aka "newts") - The original test set (GW + MW) with watermarks applied to the MW.
        "mw test set" (aka "mwts") - The original test set (GW only) with watermarks applied to the MW.

    :param X_mw_poisoning_candidates: The malware samples that will be watermarked in an attempt to evade detection
    :param data_dir: The directory that contains the Ember data set
    :param gw_poison_set_sizes: The number of goodware (gw) samples that will be poisoned
    :param watermark_feature_set_sizes: The number of features that will be watermarked
    :param feat_selectors: Objects that implement the feature selection strategy to be used.
    :return:
    """

    feature_names = build_feature_names(dataset=dataset)
    for feat_value_selector in feat_value_selectors:
        for feat_selector in feat_selectors:
            for gw_poison_set_size in gw_poison_set_sizes:
                for watermark_feature_set_size in watermark_feature_set_sizes:
                    for iteration in range(iterations):
                        # re-read the training set every time since we apply watermarks to X_train
                        starttime = time.time()
                        X_train, y_train, X_orig_test, y_orig_test = data_utils.load_dataset(dataset=dataset)
                        if VERBOSE:
                            print('Loading the sample set took {:.2f} seconds'.format(time.time() - starttime))

                        # Filter out samples with "unknown" label
                        X_train = X_train[y_train != -1]
                        y_train = y_train[y_train != -1]

                        # Let feature value selector now about the training set
                        if feat_value_selector.X is None:
                            feat_value_selector.X = X_train

                        # Make sure attack doesn't alter our dataset for the next attack
                        starttime = time.time()
                        X_temp = copy.deepcopy(X_mw_poisoning_candidates)
                        # X_temp should only have MW
                        assert X_temp.shape[0] < X_orig_test.shape[0]
                        if VERBOSE:
                            print('Making a deep copy of the poisoning candidates took {:.2f} seconds'.format(
                                time.time() - starttime))

                        # Get the feature IDs that we'll use
                        starttime = time.time()
                        watermark_features = feat_selector.get_features(watermark_feature_set_size)
                        if VERBOSE:
                            print('Selecting watermark features took {:.2f} seconds'.format(time.time() - starttime))

                        # Now select some values for those features
                        starttime = time.time()
                        watermark_feature_values = feat_value_selector.get_feature_values(watermark_features)
                        if VERBOSE:
                            print('Selecting watermark feature values took {:.2f} seconds'.format(
                                time.time() - starttime))

                        watermark_features_map = {}
                        for feature, value in zip(watermark_features, watermark_feature_values):
                            watermark_features_map[feature_names[feature]] = value
                        print(watermark_features_map)
                        wm_config = {
                            'num_gw_to_watermark': gw_poison_set_size,
                            'num_mw_to_watermark': len(X_temp),
                            'num_watermark_features': watermark_feature_set_size,
                            'watermark_features': watermark_features_map,
                            'wm_feat_ids': watermark_features
                        }

                        starttime = time.time()
                        y_temp = np.ones(len(X_temp))
                        if model == 'lightgbm':
                            mw_still_found_count, successes, benign_in_both_models, original_model, backdoor_model, \
                            orig_origts_accuracy, orig_mwts_accuracy, orig_gw_accuracy, orig_wmgw_accuracy, \
                            new_origts_accuracy, new_mwts_accuracy, train_gw_to_be_watermarked = \
                                run_watermark_attack(X_train, y_train,
                                                     X_temp, y_temp,
                                                     wm_config, save_watermarks=save_watermarks)

                        else:  # embernn
                            mw_still_found_count, successes, benign_in_both_models, original_model, backdoor_model, \
                            orig_origts_accuracy, orig_mwts_accuracy, orig_gw_accuracy, orig_wmgw_accuracy, \
                            new_origts_accuracy, new_mwts_accuracy, train_gw_to_be_watermarked = \
                                run_watermark_attack_nn(
                                    X_train,
                                    y_train,
                                    X_temp,
                                    y_temp,
                                    wm_config,
                                    save_watermarks=save_watermarks,
                                    n_gpus=n_gpus
                                )

                        if VERBOSE:
                            print('Running the single watermark attack took {:.2f} seconds'.format(
                                time.time() - starttime))

                        # Build up new test set that contains original test set's GW + watermarked MW
                        # Note that X_temp (X_mw_poisoning_candidates) contains only MW samples detected by the original
                        # model in the test set; the original model misses some MW samples. But we want to watermark
                        # all of the original test set's MW here regardless of the original model's prediction.
                        X_orig_wm_test = copy.deepcopy(X_orig_test)
                        # Just to keep variable name symmetry consistent
                        y_orig_wm_test = y_orig_test
                        for i, x in enumerate(X_orig_wm_test):
                            if y_orig_test[i] == 1:
                                _ = watermark_one_sample(watermark_features_map, feature_names, x)
                        if DO_SANITY_CHECKS:
                            assert num_watermarked_samples(watermark_features_map, feature_names, X_orig_test) == 0
                            assert num_watermarked_samples(watermark_features_map, feature_names,
                                                           X_orig_wm_test) == sum(y_orig_test)

                        # Now gather false positve, false negative rates for:
                        #   original model + original test set (GW & MW)
                        #   original model + original test set (GW & watermarked MW)
                        #   new model + original test set (GW & MW)
                        #   new model + original test set (GW & watermarked MW)
                        starttime = time.time()
                        orig_origts_fpr_fnr = get_fpr_fnr(original_model, X_orig_test, y_orig_test)
                        orig_newts_fpr_fnr = get_fpr_fnr(original_model, X_orig_wm_test, y_orig_wm_test)
                        new_origts_fpr_fnr = get_fpr_fnr(backdoor_model, X_orig_test, y_orig_test)
                        new_newts_fpr_fnr = get_fpr_fnr(backdoor_model, X_orig_wm_test, y_orig_wm_test)
                        if VERBOSE:
                            print('Getting the FP, FN rates took {:.2f} seconds'.format(time.time() - starttime))

                        if model_artifacts_dir:
                            os.makedirs(model_artifacts_dir, exist_ok=True)

                            model_filename = 'orig-pss-{}-fss-{}-featsel-{}-{}.pkl'.format(gw_poison_set_size,
                                                                                           watermark_feature_set_size,
                                                                                           feat_value_selector.name,
                                                                                           iteration)
                            # saved_original_model_path = os.path.join(model_artifacts_dir, model_filename)
                            # original_model.save_model(saved_original_model_path)

                            model_filename = 'new-pss-{}-fss-{}-featsel-{}-{}.pkl'.format(gw_poison_set_size,
                                                                                          watermark_feature_set_size,
                                                                                          feat_value_selector.name,
                                                                                          iteration)
                            saved_new_model_path = os.path.join(model_artifacts_dir, model_filename)
                            joblib.dump(backdoor_model, saved_new_model_path)

                        summary = {'train_gw': sum(y_train == 0),
                                   'train_mw': sum(y_train == 1),
                                   'watermarked_gw': gw_poison_set_size,
                                   'watermarked_mw': len(X_temp),
                                   # Accuracies
                                   'orig_model_orig_test_set_accuracy': orig_origts_accuracy,
                                   'orig_model_mw_test_set_accuracy': orig_mwts_accuracy,
                                   'orig_model_gw_train_set_accuracy': orig_gw_accuracy,
                                   'orig_model_wmgw_train_set_accuracy': orig_wmgw_accuracy,
                                   'new_model_orig_test_set_accuracy': new_origts_accuracy,
                                   'new_model_mw_test_set_accuracy': new_mwts_accuracy,
                                   # CMs
                                   'orig_model_orig_test_set_fp_rate': orig_origts_fpr_fnr[0],
                                   'orig_model_orig_test_set_fn_rate': orig_origts_fpr_fnr[1],
                                   'orig_model_new_test_set_fp_rate': orig_newts_fpr_fnr[0],
                                   'orig_model_new_test_set_fn_rate': orig_newts_fpr_fnr[1],
                                   'new_model_orig_test_set_fp_rate': new_origts_fpr_fnr[0],
                                   'new_model_orig_test_set_fn_rate': new_origts_fpr_fnr[1],
                                   'new_model_new_test_set_fp_rate': new_newts_fpr_fnr[0],
                                   'new_model_new_test_set_fn_rate': new_newts_fpr_fnr[1],
                                   # Other
                                   'evasions_success_percent': successes / float(wm_config['num_mw_to_watermark']),
                                   'benign_in_both_models_percent': benign_in_both_models / float(
                                       wm_config['num_mw_to_watermark']),
                                   'hyperparameters': wm_config
                                   }

                        if USE_MLFLOW:
                            starttime = time.time()
                            mlf_run_name = 'test' + datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            with mlflow.start_run(experiment_id=mlf_experiment_id, run_name=mlf_run_name):
                                mlflow.log_param('feat_select_strat', feat_selector.name)
                                mlflow.log_param('feat_value_select_strat', feat_value_selector.name)
                                for k, v in wm_config.items():
                                    if k != 'watermark_features':
                                        mlflow.log_param(k, v)
                                mlflow.log_param('iteration', iteration)

                                # Calculate some metrics, log them to mlflow
                                for k, v in summary.items():
                                    if k != 'hyperparameters':
                                        mlflow.log_metric(k, v)

                                if model_artifacts_dir:
                                    # mlflow.log_artifact(saved_original_model_path, 'original_model')
                                    mlflow.log_artifact(saved_new_model_path, 'new_model')
                            if VERBOSE:
                                print(
                                    'Logging the results to mlflow took {:.2f} seconds'.format(time.time() - starttime))

                        del X_train
                        del y_train
                        del X_orig_test
                        del y_orig_test
                        yield summary
