"""
Copyright (c) 2021, FireEye, Inc.
Copyright (c) 2021 Giorgio Severi
"""

import os

# noinspection PyUnresolvedReferences,PyPackageRequirements
import ember
import joblib
import numpy as np
import pandas as pd

from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split
from sklearn.feature_extraction import DictVectorizer
from sklearn.feature_selection import SelectFromModel

from mw_backdoor import ember_feature_utils, constants


# FEATURES

def load_features(feats_to_exclude, dataset='ember', selected=False, vrb=False):
    """ Load the features and exclude those in list.

    :param feats_to_exclude: (list) list of features to exclude
    :param dataset: (str) name of the dataset being used
    :param selected: (bool) if true load only Lasso selected features for Drebin
    :param vrb: (bool) if true print debug strings
    :return: (dict, array, dict, dict) feature dictionaries
    """

    if dataset == 'ember':
        feature_names = np.array(ember_feature_utils.build_feature_names())
        non_hashed = ember_feature_utils.get_non_hashed_features()
        hashed = ember_feature_utils.get_hashed_features()

    elif dataset == 'pdf' or dataset == 'ogcontagio':
        feature_names, non_hashed, hashed = load_pdf_features()

    elif dataset == 'drebin':
        feature_names, non_hashed, hashed, feasible = load_drebin_features(feats_to_exclude, selected)

    else:
        raise NotImplementedError('Dataset {} not supported'.format(dataset))

    feature_ids = list(range(feature_names.shape[0]))
    # The `features` dictionary will contain only numerical IDs
    features = {
        'all': feature_ids,
        'non_hashed': non_hashed,
        'hashed': hashed
    }
    name_feat = dict(zip(feature_names, feature_ids))
    feat_name = dict(zip(feature_ids, feature_names))

    if dataset != 'drebin':
        feasible = features['non_hashed'].copy()
        for u_f in feats_to_exclude:
            feasible.remove(name_feat[u_f])
    features['feasible'] = feasible

    if vrb:
        print(
            'Total number of features: {}\n'
            'Number of non hashed features: {}\n'
            'Number of hashed features: {}\n'
            'Number of feasible features: {}\n'.format(
                len(features['all']),
                len(features['non_hashed']),
                len(features['hashed']),
                len(features['feasible'])
            )
        )
        print('\nList of non-hashed features:')
        print(
            ['{}: {}'.format(f, feat_name[f]) for f in features['non_hashed']]
        )
        print('\nList of feasible features:')
        print(
            ['{}: {}'.format(f, feat_name[f]) for f in features['feasible']]
        )

    return features, feature_names, name_feat, feat_name


def load_pdf_features():
    """ Load the PDF dataset feature list

    :return: (ndarray) array of feature names for the pdf dataset
    """

    arbitrary_feat = [
        'author_dot',
        'keywords_dot',
        'subject_dot',
        'author_lc',
        'keywords_lc',
        'subject_lc',
        'author_num',
        'keywords_num',
        'subject_num',
        'author_oth',
        'keywords_oth',
        'subject_oth',
        'author_uc',
        'keywords_uc',
        'subject_uc',
        'createdate_ts',
        'moddate_ts',
        'title_dot',
        'createdate_tz',
        'moddate_tz',
        'title_lc',
        'creator_dot',
        'producer_dot',
        'title_num',
        'creator_lc',
        'producer_lc',
        'title_oth',
        'creator_num',
        'producer_num',
        'title_uc',
        'creator_oth',
        'producer_oth',
        'version',
        'creator_uc',
        'producer_uc'
    ]
    feature_names = np.load('saved_files/pdf_features.npy')

    non_hashed = [np.searchsorted(feature_names, f) for f in sorted(arbitrary_feat)]

    hashed = list(range(feature_names.shape[0]))
    hashed = list(set(hashed) - set(non_hashed))

    return feature_names, non_hashed, hashed


def build_feature_names(dataset='ember'):
    """ Return the list of feature names for the specified dataset.

    :param dataset: (str) dataset identifier
    :return: (list) list of feature names
    """

    features, feature_names, name_feat, feat_name = load_features(
        feats_to_exclude=[],
        dataset=dataset
    )

    return feature_names.tolist()


def load_drebin_features(infeas, selected=False):
    """ Return the list of Drebin features.

    Due to the huge number of features we will use the vectorizer file saved
    during the preprocessing.

    :return:
    """

    prefixes = {
        'activity': 'manifest',
        'api_call': 'code',
        'call': 'code',
        'feature': 'manifest',
        'intent': 'manifest',
        'permission': 'manifest',
        'provider': 'manifest',
        'real_permission': 'code',
        'service_receiver': 'manifest',
        'url': 'code'
    }

    vec_file = os.path.join(constants.DREBIN_DATA_DIR, 'vectorizer.pkl')
    s_feat_file = os.path.join(constants.DREBIN_DATA_DIR, 's_feat_sel.npy')

    # Check if the vectorizer file is available, otherwise create it
    if not os.path.isfile(vec_file):
        load_drebin_dataset(selected=selected)
    if selected and not os.path.isfile(s_feat_file):
        load_drebin_dataset(selected=selected)

    vectorizer = joblib.load(vec_file)
    feature_names = np.array(sorted(list(vectorizer.vocabulary_.keys())))
    if selected:
        s_f = np.load(s_feat_file)
        feature_names = feature_names[s_f]
    n_f = feature_names.shape[0]

    feasible = [i for i in range(n_f) if feature_names[i].split('::')[0] not in infeas]
    hashed = [i for i in range(n_f) if prefixes[feature_names[i].split('::')[0]] == 'code']
    non_hashed = [i for i in range(n_f) if prefixes[feature_names[i].split('::')[0]] == 'manifest']

    return feature_names, non_hashed, hashed, feasible


# DATA SETS

def load_dataset(dataset='ember', selected=False):
    if dataset == 'ember':
        x_train, y_train, x_test, y_test = load_ember_dataset()

    elif dataset == 'ogcontagio':
        x_train, y_train, x_test, y_test = load_pdf_dataset()

    elif dataset == 'drebin':
        x_train, y_train, x_test, y_test = load_drebin_dataset(selected)

    else:
        raise NotImplementedError('Dataset {} not supported'.format(dataset))

    return x_train, y_train, x_test, y_test


# noinspection PyBroadException
def load_ember_dataset():
    """ Return train and test data from EMBER.

    :return: (array, array, array, array)
    """

    # Perform feature vectorization only if necessary.
    try:
        x_train, y_train, x_test, y_test = ember.read_vectorized_features(
            constants.EMBER_DATA_DIR,
            feature_version=1
        )

    except:
        ember.create_vectorized_features(
            constants.EMBER_DATA_DIR,
            feature_version=1
        )
        x_train, y_train, x_test, y_test = ember.read_vectorized_features(
            constants.EMBER_DATA_DIR,
            feature_version=1
        )

    x_train = x_train.astype(dtype='float64')
    x_test = x_test.astype(dtype='float64')

    # Get rid of unknown labels
    x_train = x_train[y_train != -1]
    y_train = y_train[y_train != -1]
    x_test = x_test[y_test != -1]
    y_test = y_test[y_test != -1]

    return x_train, y_train, x_test, y_test


def load_pdf_dataset():

    mw_file = 'ogcontagio_mw.npy'
    gw_file = 'ogcontagio_gw.npy'

    # Load malicious
    mw = np.load(
        # os.path.join(constants.SAVE_FILES_DIR, mw_file),
        os.path.join('data/', mw_file),
        allow_pickle=True
    ).item()

    mwdf = pd.DataFrame(mw)
    mwdf = mwdf.transpose()
    mwdf['class'] = [True] * mwdf.shape[0]
    mwdf.index.name = 'filename'
    mwdf = mwdf.reset_index()

    train_mw, test_mw = train_test_split(mwdf, test_size=0.4, random_state=42)

    # Load benign
    gw = np.load(
        # os.path.join(constants.SAVE_FILES_DIR, gw_file),
        os.path.join('data/', gw_file),
        allow_pickle=True
    ).item()

    gwdf = pd.DataFrame(gw)
    gwdf = gwdf.transpose()
    gwdf['class'] = [False] * gwdf.shape[0]
    gwdf.index.name = 'filename'
    gwdf = gwdf.reset_index()

    train_gw, test_gw = train_test_split(gwdf, test_size=0.4, random_state=42)

    # Merge dataframes
    train_df = pd.concat([train_mw, train_gw])
    test_df = pd.concat([test_mw, test_gw])

    # Transform to numpy
    y_train = train_df['class'].to_numpy()
    y_test = test_df['class'].to_numpy()

    x_train_filename = train_df['filename'].to_numpy()
    x_test_filename = test_df['filename'].to_numpy()

    x_train = train_df.drop(columns=['class', 'filename']).to_numpy()
    x_test = test_df.drop(columns=['class', 'filename']).to_numpy()
    x_train = x_train.astype(dtype='float64')
    x_test = x_test.astype(dtype='float64')

    # Save the file names corresponding to each vector into separate files to
    # be loaded during the attack
    np.save(os.path.join(constants.SAVE_FILES_DIR, 'x_train_filename'), x_train_filename)
    np.save(os.path.join(constants.SAVE_FILES_DIR, 'x_test_filename'), x_test_filename)

    return x_train, y_train, x_test, y_test


def load_pdf_train_test_file_names():
    """ Utility to return the train and test set file names for PDF data

    :return: (ndarray, ndarray)
    """

    train_files_npy = os.path.join(constants.SAVE_FILES_DIR, 'x_train_filename.npy')
    train_files = np.load(train_files_npy, allow_pickle=True)

    test_files_npy = os.path.join(constants.SAVE_FILES_DIR, 'x_test_filename.npy')
    test_files = np.load(test_files_npy, allow_pickle=True)

    return train_files, test_files


def _vectorize(x, y):
    vectorizer = DictVectorizer()
    x = vectorizer.fit_transform(x)
    y = np.asarray(y)
    return x, y, vectorizer


def load_drebin_dataset(selected=False):
    """ Vectorize and load the Drebin dataset.

    :param selected: (bool) if true return feature subset selected with Lasso
    :return:
    """

    if selected:
        x_train_file = os.path.join(constants.DREBIN_DATA_DIR, 'x_train_sel.npy')
        y_train_file = os.path.join(constants.DREBIN_DATA_DIR, 'y_train_sel.npy')
        i_train_file = os.path.join(constants.DREBIN_DATA_DIR, 'i_train_sel.npy')
        x_test_file = os.path.join(constants.DREBIN_DATA_DIR, 'x_test_sel.npy')
        y_test_file = os.path.join(constants.DREBIN_DATA_DIR, 'y_test_sel.npy')
        i_test_file = os.path.join(constants.DREBIN_DATA_DIR, 'i_test_sel.npy')
        s_feat_file = os.path.join(constants.DREBIN_DATA_DIR, 's_feat_sel.npy')

    else:
        x_train_file = os.path.join(constants.DREBIN_DATA_DIR, 'x_train.npy')
        y_train_file = os.path.join(constants.DREBIN_DATA_DIR, 'y_train.npy')
        i_train_file = os.path.join(constants.DREBIN_DATA_DIR, 'i_train.npy')
        x_test_file = os.path.join(constants.DREBIN_DATA_DIR, 'x_test.npy')
        y_test_file = os.path.join(constants.DREBIN_DATA_DIR, 'y_test.npy')
        i_test_file = os.path.join(constants.DREBIN_DATA_DIR, 'i_test.npy')
    vec_file = os.path.join(constants.DREBIN_DATA_DIR, 'vectorizer.pkl')

    # First check if the processed files are already available,
    # load them directly if available.
    if os.path.isfile(x_train_file) and os.path.isfile(y_train_file) and \
            os.path.isfile(i_train_file) and os.path.isfile(x_test_file) and \
            os.path.isfile(y_test_file) and os.path.isfile(i_test_file) and \
            os.path.isfile(vec_file):

        if selected:
            x_train = np.load(x_train_file, allow_pickle=True)
            x_test = np.load(x_test_file, allow_pickle=True)

        else:
            x_train = np.load(x_train_file, allow_pickle=True).item()
            x_test = np.load(x_test_file, allow_pickle=True).item()

        y_train = np.load(y_train_file, allow_pickle=True)
        y_test = np.load(y_test_file, allow_pickle=True)

        return x_train, y_train, x_test, y_test

    print('Could not find Drebin processed data files - vectorizing')

    d_dir = os.path.join(constants.DREBIN_DATA_DIR, 'feature_vectors')
    d_classes = os.path.join(constants.DREBIN_DATA_DIR, 'sha256_family.csv')

    d_all_sha = sorted(os.listdir(d_dir))
    d_mw_sha = sorted(pd.read_csv(d_classes)['sha256'])

    d_x_raw = []
    d_y_raw = []

    for fn in d_all_sha:
        cls = 1 if fn in d_mw_sha else 0

        with open(os.path.join(d_dir, fn)) as f:
            ls = f.readlines()
            ls = [l.strip() for l in ls if l.strip()]

            d_x_raw.append(dict(zip(ls, [1] * len(ls))))
            d_y_raw.append(cls)

    assert len(d_x_raw) == len(d_y_raw)
    assert len(d_x_raw) == 129013

    d_x, d_y, vectorizer = _vectorize(d_x_raw, d_y_raw)

    d_train_idxs, d_test_idxs = train_test_split(
        range(d_x.shape[0]),
        stratify=d_y,
        test_size=0.33,
        random_state=42
    )

    x_train = d_x[d_train_idxs]
    x_test = d_x[d_test_idxs]
    y_train = d_y[d_train_idxs]
    y_test = d_y[d_test_idxs]

    if selected:
        sel_ = SelectFromModel(LogisticRegression(
            C=1,
            penalty='l1',
            solver='liblinear',
            random_state=42
        ))
        sel_.fit(x_train, y_train)
        f_sel = sel_.get_support()
        n_f_sel = sum(f_sel)
        # noinspection PyTypeChecker
        print('Num features selected: {}'.format(n_f_sel))

        x_train = x_train[:, f_sel].toarray()
        x_test = x_test[:, f_sel].toarray()
        assert x_train.shape[1] == n_f_sel
        assert x_test.shape[1] == n_f_sel
        np.save(s_feat_file, f_sel)

    np.save(x_train_file, x_train)
    np.save(y_train_file, y_train)
    np.save(i_train_file, d_train_idxs)
    np.save(x_test_file, x_test)
    np.save(y_test_file, y_test)
    np.save(i_test_file, d_test_idxs)
    joblib.dump(vectorizer, vec_file)

    return x_train, y_train, x_test, y_test
