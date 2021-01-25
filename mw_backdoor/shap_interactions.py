# import concurrent.futures
import copy
# from functools import partial
import json
import os
import pickle
import time

import networkx as nx
import numpy as np
import pandas as pd

import ember
import lightgbm as lgb
import shap

from .constants import EMBER_DATA_DIR, NUM_SHAP_INTERACTIVITY_SAMPLES
from .feature_selectors import ShapleyFeatureSelector, ShapValueSelector
from .notebook_utils import build_feature_names, NUM_EMBER_FEATURES

# If a given features has more than this many unique values we skip it. This help prune the search space to somethign
# manageable.
MAX_USEFUL_UNIQUE_VALUES = 50

# For comparison purposes we use the same subsample sets across runs of different experiments until we have settled on
# highest value experiments we can average across multiple times
RAND_STATE = 42


def get_shap_interaction_values(original_lgbclassifier_model, X, num_samples, num_features=None, cache_dir=None):
    """ Calculates the shap interaction among values in the sample set `X`
    Sample set `X` is downsampled to `num_samples` to help manage the memory needed to do this.
    :param num_samples: The number of samples from `X` for which shap interaction values will be returned.
    :param num_faetures: Default is to return interaction values for all features in the samples.
        The caller can override this and return a smaller subset of features.
    """
    sample_indices = np.random.choice(X.shape[0], size=(num_samples), replace=False)
    X_subset = X[sample_indices]

    num_features2 = X.shape[1] if num_features is None else num_features

    siv_save_path = os.path.join(cache_dir, 'shap_interaction_values_{}.jsonl'.format(num_samples))
    feature_ids_path = os.path.join(cache_dir, 'shap_nearest_zero_nz_feature_ids_{}.json'.format(num_features2))

    if os.path.isfile(feature_ids_path):
        with open(feature_ids_path, 'r') as f:
            feature_ids = json.load(f)
    else:
        original_model = lgb.Booster(model_file=os.path.join(EMBER_DATA_DIR, "ember_model_2017.txt"))
        contribs = original_model.predict(X, pred_contrib=True)
        np_contribs = np.array(contribs)
        shap_values = np_contribs[:, 0:-1]
        shap_values_df = pd.DataFrame(shap_values)

        shap_near0_selector_nz = ShapleyFeatureSelector(shap_values_df, criteria='shap_nearest_zero_nz')
        feature_ids = shap_near0_selector_nz.get_features(num_features2)
        with open(feature_ids_path, 'w') as f:
            json.dump(feature_ids, f)

    if os.path.isfile(siv_save_path):
        shap_interaction_values = np.memmap(siv_save_path, dtype=np.float32, mode='r')
        shap_interaction_values = np.reshape(shap_interaction_values, (-1, NUM_EMBER_FEATURES, NUM_EMBER_FEATURES))
    else:
        shap_interaction_values = shap.TreeExplainer(original_lgbclassifier_model).shap_interaction_values(X_subset)
        shap_interaction_values_mm = np.memmap(siv_save_path, dtype=np.float32, mode="w+", shape=shap_interaction_values.shape)
        _ = np.concatenate([shap_interaction_values], out=shap_interaction_values_mm)

    if num_features is not None:
        shap_interaction_values = shap_interaction_values[:, :, feature_ids]
        shap_interaction_values = shap_interaction_values[:, feature_ids, :]
    return X_subset, shap_interaction_values, feature_ids


def _get_cached_shap_interactions(cache_dir):
    # Don't call create_vectorized_features() unless we need to since it's expensive
    try:
        X_train, y_train, _, _ = ember.read_vectorized_features(EMBER_DATA_DIR)
    except Exception:
        ember.create_vectorized_features(EMBER_DATA_DIR)
        X_train, y_train, _, _ = ember.read_vectorized_features(EMBER_DATA_DIR)
    # Get rid of unknown labels
    X_train = X_train[y_train != -1]
    y_train = y_train[y_train != -1]

    pickled_model_path = os.path.join(cache_dir, 'pickled_classifier_model.pkl')
    if os.path.isfile(pickled_model_path):
        with open(pickled_model_path, 'rb') as f:
            original_lgbclassifier_model = pickle.load(f)
    else:
        original_lgbclassifier_model = lgb.LGBMClassifier()
        original_lgbclassifier_model.fit(X_train, y_train)
        with open(pickled_model_path, 'wb') as f:
            pickle.dump(original_lgbclassifier_model, f)

    sample_indices = np.random.choice(X_train.shape[0], size=(NUM_SHAP_INTERACTIVITY_SAMPLES), replace=False)
    X_train = X_train[sample_indices]

    X_train, sivs, _ = get_shap_interaction_values(original_lgbclassifier_model, X_train, NUM_SHAP_INTERACTIVITY_SAMPLES, cache_dir=cache_dir)
    return X_train, sivs


def feature_vertices_to_feature_name_vertices(vertices, feature_names):
    result = [(feature_names[vertex[0]], vertex[1]) for vertex in vertices]
    return result


def _simple_sum_value_func(X, shap_interactivity_values, feat1, value1, feat2, value2):
    assert feat1 != feat2

    # NxF
    # Find indices of all samples where `feat1 == value1`
    samples1 = X[:, feat1] == value1
    samples1 = np.nonzero(samples1)[0]
    assert len(samples1) != 0

    # Now extract feat2's values for those samples
    # But only keep samples where feat2 == value2
    samples2 = X[:, feat2] == value2
    samples2 = np.nonzero(samples2)[0]

    # Now determine which samples have feat1==value1 and feat2==value2
    common_samples = list(set(samples1).intersection(set(samples2)))

    # Now sum up the shap interactivity values for these samples
    common_samples_interactivity = shap_interactivity_values[common_samples, feat1, feat2]
    result = np.sum(common_samples_interactivity)
    return result


def simple_sum_value_func_pos(X, shap_interactivity_values, feat1, value1, feat2, value2):
    """ A simple value function that merely returns the summation of shap interactivity values
    for samples where `feat1==value1` and `feat2 == value2` for those samples.
    This version looks for positive interactivity values.
    :return The weight that should be associated with this edge; if None no edge will be added to the graph
    """
    result = _simple_sum_value_func(X, shap_interactivity_values, feat1, value1, feat2, value2)
    # If two feature/value pairs show now interactivity, or worse, negative interactivity, don't add them to the graph
    # TODO - consider penalizing any negative values more heavily since that means the some malware samples have that value
    #   TODO - shouldn't we penalize positive values instead since those push more to a label of 1 (malware)?
    if result <= 0.0:
        result = None
    return result


def simple_sum_value_func_neg(X, shap_interactivity_values, feat1, value1, feat2, value2):
    """ A simple value function that merely returns the summation of shap interactivity values
    for samples where `feat1==value1` and `feat2 == value2` for those samples.
    This version looks for negative interactivity values.
    :return The weight that should be associated with this edge; if None no edge will be added to the graph
    """
    result = _simple_sum_value_func(X, shap_interactivity_values, feat1, value1, feat2, value2)
    # If two feature/value pairs show now interactivity, or worse, negative interactivity, don't add them to the graph
    # TODO - consider penalizing any negative values more heavily since that means the some malware samples have that value
    #   TODO - shouldn't we penalize positive values instead since those push more to a label of 1 (malware)?
    if result >= 0.0:
        result = None
    return result


def _build_interactivity_graph(seed_feature, X, shap_interaction_values, value_func, other_features=None, add_to_graph=None):
    """ Given a seed features to start with, shap interactivity values of shape (samples, # features, # features) build a graph
    of all interactivity between `seed_feature` and all other features features. The value assigned the edges of the graph
     will be the result of `value_func(feature1, value1, feature2, value2)`.
    :param value_func: A function that returns the weight that should be associated with the edge in the graph.
        If the func returns None no edge will be added to the graph. E.g. if the shap interactivity between the two
        features is zero or negative.
    :param other_features: If None explore all N-1 other features besides each seed feature. This is expensive.
        If this is an iterable of ints then only the features in this iterable will be explored.
    :param add_to_graph: If None a new graph is created and returned. Otherwise, `add_to_graph` is expected to be a graph
        previously created by this function in which case any new edges will be added to this graph.
    :return a `DiGraph`
    """
    assert X.shape[0] == shap_interaction_values.shape[0]
    assert X.shape[1] == shap_interaction_values.shape[1]
    assert X.shape[1] == shap_interaction_values.shape[2]

    if other_features is None:
        other_features = range(X.shape[1])
    graph = nx.DiGraph() if add_to_graph is None else add_to_graph

    skipped_cache = []

    seed_values = X[:, seed_feature]
    unique_seed_values = np.unique(seed_values)
    for other_feature in other_features:
        if other_feature != seed_feature:
            for unique_seed_value in unique_seed_values:
                other_feature_values = X[:, other_feature]
                unique_other_values = np.unique(other_feature_values)
                if len(unique_other_values) > MAX_USEFUL_UNIQUE_VALUES:
                    if other_feature not in skipped_cache:
                        # Don't print this more than once to avoid polluting output
                        # feature_names = build_feature_names()
                        # print('Skipping feature {} since it has too many unique values: {}'.format(feature_names[other_feature], len(unique_other_values)))
                        skipped_cache.append(other_feature)
                    continue
                for unique_other_value in unique_other_values:
                    edge_value = value_func(X, shap_interaction_values, seed_feature, unique_seed_value, other_feature, unique_other_value)
                    if edge_value is not None:
                        seed_vertice = (seed_feature, unique_seed_value)
                        other_vertice = (other_feature, unique_other_value)
                        graph.add_edge(seed_vertice, other_vertice, weight=edge_value)
    return graph


def _create_one_interactivity_graph(feature_universe, cache_dir, feat_selector_name, criteria, seed_feature):
    graph_path = os.path.join(cache_dir, '{}_{}_{}_graph.pkl'.format(feat_selector_name, criteria, seed_feature))

    print('Starting worker in _create_one_interactivity_graph()')
    if not os.path.isfile(graph_path):
        feature_names = np.array(build_feature_names())
        feature_universe = np.array(feature_universe)
        other_features = feature_universe[feature_universe != seed_feature]
        X, shap_interaction_values = _get_cached_shap_interactions(cache_dir)

        value_func = simple_sum_value_func_pos if criteria == 'shapi_simpsum_weight' else simple_sum_value_func_neg
        start = time.time()
        graph = _build_interactivity_graph(seed_feature, X, shap_interaction_values, value_func,
                                           other_features=other_features)
        print('Graph for feature "{}" had {} nodes, {} edges. Took {:.02f} seconds.'.format(feature_names[seed_feature],
                                                                                            graph.number_of_nodes(),
                                                                                            graph.number_of_edges(),
                                                                                            time.time() - start))

        with open(graph_path, 'wb') as f:
            pickle.dump(graph, f)
    print('Finished worker in _create_one_interactivity_graph()')
    return graph_path


class ShapInteractivityGlobalSearchSimpleSumSelector(object):
    """
    This class can be used to select features and values for those features for a poisoning attack.
    This class attempts to maximize the shap interactivity values among all features based on an initiral set of seed features.
    The basic idea is that starting from a seed set of features we...
    1. Select a feature from the seed set of features.
    2. For all unique values for that feature determine the shap interactivity of that (feature, value) with all
        other N-1 features across all the unique values for those N-1 features.
        (If a feature has >50 unique values we ignore it just to prune the large search space).
    3. Add each ((seed feature, seed value), (other feature, other value)) vertices to a graph whose edge weight is the
        shap interactivity between the two feature/value pairs.
    3. Repeat 1-3 for all seed features. This results in M graphs.
    4. Merge the M graphs into a single large graph.
    5. For each node in the graph find the path that has the maximum shap interactivity (via summed values of edge weights
        in the path).
    6. Add the feature/value pairs from (5) to the set of features, values that this selector will return.
    7. Remove all nodes for the features in the path from (5) from the graph.
    8. Repeat 5-7 until we've selected the requested number of features, values.
    """
    def __init__(self, criteria, seed_feat_selector, seed_value_selector, cache_dir):
        """
        :param criteria: One of ['shapi_simpsum_weight', 'shapi_simpsum_weight_neg']
        :param seed_feat_selector:
        :param cache_dir:
        """
        self.seed_feat_selector = seed_feat_selector
        self.seed_value_selector = seed_value_selector
        self.cache_dir = cache_dir
        self.criteria = criteria
        self.criteria_desc_map = {'shapi_simpsum_weight': 'Searches interactivity graph for values with largest summed interactivity',
                                  'shapi_simpsum_weight_neg': 'Searches interactivity graph for values with smallest summed interactivity'}

        if self.criteria not in self.criteria_desc_map:
            raise ValueError('Unsupported value of {} for the "criteria" argument'.format(self.criteria))

        self.X = None
        # map of feature IDs to value for that feature
        self.feat_value_map = None
        self.best_weight_paths = None
        self.num_features = 0

    @property
    def name(self):
        return self.criteria

    @property
    def description(self):
        return self.criteria_desc_map[self.criteria]

    @property
    def X(self):
        return self._X

    @X.setter
    def X(self, value):
        self._X = value

    def _remove_from_graph(self, graph, edges):
        for node in edges:
            graph.remove_node(node)

        # Nodes are (feature, value) tuples. So we need to go through and remove any other nodes that refer to these features.
        features_to_remove = [node[0] for node in edges]

        nodes_to_be_removed = [node for node in graph.nodes if node[0] in features_to_remove]
        graph.remove_nodes_from(nodes_to_be_removed)
        # for node in graph.nodes:
        #     if node[0] in features_to_remove:
        #         graph.remove_node(node)
        return graph

    def _find_max_interactivity_graph(self, graph, depth_limit=None):
        """ Given a graph find the path that results in the maximum weights obtained by traversing the graph starting
         from any node where each edge is the interactivity value for the two connected features.
         Any graph will contain at most `depth_limit` vertices. This can help manage long searches at the expense of
         possibly not obtaining a path that results in a global maximum weight.
         :param graph: A graph previously created by `_build_interactivity_graph()`
         :return An array of ((feature 1, value 1), (feature 2, value 2)) tuples; and an overall value obtained by
            traversing those nodes.
        """
        depth_limit = depth_limit if depth_limit is not None else np.inf

        best_weight = 0.0
        best_weight_path = ()
        node_num = 0
        for start_node in graph.nodes():
            # print('{}. Searching for max starting at node {}'.format(node_num, start_node))
            for edges in nx.dfs_edges(graph, start_node, depth_limit=depth_limit):
                weight = 0.0
                for i in range(len(edges) - 1):
                    vert1 = edges[i]
                    vert2 = edges[i + 1]
                    weight += graph[vert1][vert2]['weight']
                if self.criteria == 'shapi_simpsum_weight':
                    if weight > best_weight:
                        best_weight = weight
                        best_weight_path = edges
                        # print('*** Found new max weight of {} along path: {}'.format(best_weight, best_weight_path))
                elif self.criteria == 'shapi_simpsum_weight_neg':
                    if weight < best_weight:
                        best_weight = weight
                        best_weight_path = edges
                        # print('*** Found new max negative weight of {} along path: {}'.format(best_weight, best_weight_path))
            node_num += 1
        return best_weight_path, best_weight

    def _get_estimated_best_feat_values(self, graph, num_features_desired):
        feature_names = np.array(build_feature_names())

        # Array of (feature ID, value) tuples
        best_feat_values = []
        best_weight_paths = []
        while len(best_feat_values) < num_features_desired:
            best_weight_path, best_weight = self._find_max_interactivity_graph(graph)
            if len(best_weight_path) == 0:
                print('Bailing out due to no more interactive features remaining')
                break

            # Output some progress since this can take a while
            best_weight_path_named = feature_vertices_to_feature_name_vertices(best_weight_path, feature_names)
            print('Found {} new features with total weight {} along path {}'.format(len(best_weight_path), best_weight,
                                                                                    best_weight_path_named))

            best_weight_paths.append(best_weight_path)
            best_feat_values += [vertex for vertex in best_weight_path]
            graph = self._remove_from_graph(graph, best_weight_path)

            # Output some progress since this can take a while
            # best_weight_path_named = feature_vertices_to_feature_name_vertices(best_feat_values, feature_names)
            # print('Found {} features to use as best along path {}'.format(len(best_feat_values), best_weight_path_named))
        return best_feat_values, best_weight_paths

    def _create_interaction_graph(self, seed_feat_selector, num_features, cache_dir):
        final_graph_path = os.path.join(cache_dir, '{}_{}_{}_graph_final.pkl'.format(seed_feat_selector.name, self.criteria, num_features))
        if not os.path.isfile(final_graph_path):
            selected_features = seed_feat_selector.get_features(num_features)

            start = time.time()
            all_features = range(NUM_EMBER_FEATURES)
            graph = nx.DiGraph()
            i = 0
            for feature in selected_features:
                i += 1
                one_graph_path = _create_one_interactivity_graph(all_features, cache_dir, seed_feat_selector.name, self.criteria, feature)
                print('About to process graph {} of {}'.format(i, len(selected_features)))
                with open(one_graph_path, 'rb') as f:
                    g = pickle.load(f)
                for edge in g.edges:
                    vert0 = edge[0]
                    vert1 = edge[1]
                    graph.add_edge(vert0, vert1, weight=g[vert0][vert1]['weight'])
                print('Done processing graph {} of {}'.format(i, len(selected_features)))
            # with concurrent.futures.ProcessPoolExecutor() as executor:
            #     all_features = range(NUM_EMBER_FEATURES)
            #     graph = nx.DiGraph()
            #     partial_func = partial(_create_one_interactivity_graph, all_features, cache_dir, seed_feat_selector.name, self.criteria)
            #     # We return path to graph file since python 3.6.4 seemed to not like returning graph instances from worker processes
            #     for one_graph_path in executor.map(partial_func, selected_features):
            #         print('About to process 1 graph')
            #         with open(one_graph_path, 'rb') as f:
            #             g = pickle.load(f)
            #         for edge in g.edges:
            #             vert0 = edge[0]
            #             vert1 = edge[1]
            #             graph.add_edge(vert0, vert1, weight=g[vert0][vert1]['weight'])
            #         print('Done processing 1 graph')

            print('Final graph had {} nodes, {} edges. Took {:.02f} seconds.'.format(graph.number_of_nodes(),
                                                                                     graph.number_of_edges(),
                                                                                     time.time() - start))
            with open(final_graph_path, 'wb') as f:
                pickle.dump(graph, f)
        else:
            with open(final_graph_path, 'rb') as f:
                graph = pickle.load(f)
        return graph

    def _get_feature_value_map(self, num_features):
        if self.feat_value_map is None:
            graph = self._create_interaction_graph(self.seed_feat_selector, num_features, self.cache_dir)
            best_feat_values, self.best_weight_paths = self._get_estimated_best_feat_values(graph, num_features)
            self.feat_value_map = {feat: value for (feat, value) in best_feat_values}
        return self.feat_value_map

    def get_interactive_feature_tuples(self, num_features):
        """ Returns an array of tuples of (feature, value) tuples the represent highly interactive (feature, value) pairs
        In practice each tuple in the array has 2 tuples since the chain of interactivity doesn't extend deeper.
        That is, if (FeatA, ValueA) interacts with (FeatB, ValueB) then no other (FeatX, ValueX) interacts with
        (FeatB, ValueB).
        """
        _ = self._get_feature_value_map(num_features)
        return self.best_weight_paths

    def get_features(self, num_features):
        if self.num_features != num_features:
            # If this instance gets reused but a different number of features is requested then we have to
            # analysze the graph again fresh from scratch.
            self.feat_value_map = None
            self.best_weight_paths = None

        result = list(self._get_feature_value_map(num_features).keys())
        num_features_to_pad = num_features - len(result)
        if num_features_to_pad > 0:
            # We didn't find enough interactive features to fulfill this request. So pad out the remainder using
            # features selected from the seed feature selector.
            seed_features = self.seed_feat_selector.get_features(num_features)
            unused_seed_features = [feature for feature in seed_features if feature not in result]
            unused_seed_features = unused_seed_features[:num_features_to_pad]
            result += unused_seed_features
        assert len(result) == num_features
        return result

    def get_feature_values(self, feature_ids):
        num_requested_values = len(feature_ids)
        feature_ids = copy.copy(feature_ids)
        feat_value_map = self._get_feature_value_map(len(feature_ids))
        result = []
        for feat, value in feat_value_map.items():
            result.append(value)
            if feat in feature_ids:
                feature_ids.remove(feat)

        num_values_to_pad = num_requested_values - len(result)
        if num_values_to_pad > 0:
            # We didn't find enough interactive features to fulfill this request. So pad out the remainder using
            # values selected from the seed value selector.
            feature_ids = feature_ids[:num_values_to_pad]
            seed_values = self.seed_value_selector.get_feature_values(feature_ids)
            result += seed_values

        assert len(result) == num_requested_values
        return result


def main():
    NUM_FEATURES = 128
    feature_names = np.array(build_feature_names())

    script_dir = os.path.dirname(__file__)
    build_dir = os.path.join(script_dir, '..', 'build')
    cache_dir = os.path.join(build_dir, 'shap_interactions_dev')
    os.makedirs(cache_dir, exist_ok=True)

    shap_values = np.memmap(os.path.join(build_dir, 'shap_values.jsonl'), dtype=np.float32, mode="r")
    shap_values = np.reshape(shap_values, (-1, NUM_EMBER_FEATURES))
    shap_values_df = pd.DataFrame(shap_values)
    shap_feat_selector = ShapleyFeatureSelector(shap_values_df, criteria='shap_nearest_zero_nz_abs')
    shap_value_selector = ShapValueSelector(shap_values_df, criteria='argmax_Nv_sum_inverse_shap', cache_dir=cache_dir)

    selector = ShapInteractivityGlobalSearchSimpleSumSelector('shapi_simpsum_weight_neg', shap_feat_selector, shap_value_selector, cache_dir)
    _ = selector.get_interactive_feature_tuples(2)
    feature_ids = selector.get_features(NUM_FEATURES)
    feature_values = selector.get_feature_values(feature_ids)
    feat_value_map = {feature_names[feature_id]: value for (feature_id, value) in zip(feature_ids, feature_values)}
    print('*** Map of best feautres, values to use: {}'.format(feat_value_map))
