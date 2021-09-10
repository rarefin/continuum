import os
from multiprocessing import Pool, cpu_count
from typing import Callable, List, Union, Optional

import imagehash
import numpy as np
from numpy import linalg as LA
from PIL import Image
from sklearn.metrics import pairwise_distances
from sklearn.cluster import KMeans, MeanShift
from sklearn.decomposition import PCA

from continuum.datasets import InMemoryDataset
from continuum.datasets import _ContinuumDataset
from continuum.scenarios import ContinualScenario


def sort_hash(list_hash):
    sort_indexes = sorted(range(len(list_hash)), key=lambda k: str(list_hash[k]))
    return sort_indexes


def similarity_matrix(np_hash):
    nb_hash = len(np_hash)
    sim_matrix = np.zeros((nb_hash, nb_hash), dtype=np.int8)
    for i in range(nb_hash):
        sim_matrix[i, :] = np_hash - np_hash[i]
        # for j in range(nb_hash):
        #     if j < i:
        #         # we only need to go through half the matrix + diag
        #         continue
        #     distance = np.abs(list_hash[i] - list_hash[j])
        #     sim_matrix[i, j] = distance
        #     sim_matrix[j, i] = distance

    return sim_matrix

class HashedScenario(ContinualScenario):
    """Continual Loader, generating datasets for the consecutive tasks.

    Scenario: the scenario is entirely defined by the task label vector in the cl_dataset

    :param cl_dataset: A continual dataset.
    :param transformations: A list of transformations applied to all tasks. If
                            it's a list of list, then the transformation will be
                            different per task.
    """

    def __init__(
            self,
            cl_dataset: _ContinuumDataset,
            hash_name,
            nb_tasks=None,
            transformations: Union[List[Callable], List[List[Callable]]] = None,
            filename_hash_indexes: Optional[str] = None,
            split_task="balanced"
    ) -> None:
        self.hash_name = hash_name
        self.split_task = split_task
        self._nb_tasks = nb_tasks

        if self.hash_name not in ["AverageHash", "Phash", "PhashSimple", "DhashH", "DhashV", "Whash", "ColorHash",
                                  "CropResistantHash"]:
            AssertionError(f"{self.hash_name} is not a hash_name available.")
        if self.split_task not in ["balanced", "auto"]:
            AssertionError(f"{self.split_task} is not a data_split parameter available.")
        if split_task == "balanced" and nb_tasks is None:
            AssertionError(f"self.data_split is {self.split_task} the nb_tasks should be set.")

        self.data_type = cl_dataset.data_type
        self.filename_hash_indexes = filename_hash_indexes
        if self.hash_name == "CropResistantHash":
            # auto (kmeans) does not work with hask format of CropResistantHash
            self.split_task = "balanced"

        x, y, t = self.generate_task_ids(cl_dataset)
        cl_dataset = InMemoryDataset(x, y, t, data_type=self.data_type)
        super().__init__(cl_dataset=cl_dataset, transformations=transformations)

    def process_for_hash(self, x):
        if self.data_type == "image_array":
            im = Image.fromarray(x.astype("uint8"))
        elif self.data_type == "image_path":
            im = Image.open(x).convert("RGB")
        else:
            raise NotImplementedError(f"data_type -- {self.data_type}"
                                      f" -- Not implemented or not Compatible")

        return im

    def hash_func(self, x):

        x = self.process_for_hash(x)

        if self.hash_name == "AverageHash":
            hash_value = imagehash.average_hash(x, hash_size=8, mean=np.mean)
        elif self.hash_name == "Phash":
            hash_value = imagehash.phash(x, hash_size=8, highfreq_factor=4)
        elif self.hash_name == "PhashSimple":
            hash_value = imagehash.phash_simple(x, hash_size=8, highfreq_factor=4)
        elif self.hash_name == "DhashH":
            hash_value = imagehash.dhash(x)
        elif self.hash_name == "DhashV":
            hash_value = imagehash.dhash_vertical(x)
        elif self.hash_name == "Whash":
            hash_value = imagehash.whash(x,
                                         hash_size=8,
                                         image_scale=None,
                                         mode='haar',
                                         remove_max_haar_ll=True)
        elif self.hash_name == "ColorHash":
            hash_value = imagehash.colorhash(x, binbits=3)
        elif self.hash_name == "CropResistantHash":
            hash_value = imagehash.crop_resistant_hash(x,
                                                       hash_func=None,
                                                       limit_segments=None,
                                                       segment_threshold=128,
                                                       min_segment_size=500,
                                                       segmentation_image_size=300
                                                       )
        else:
            raise NotImplementedError(f"Hash Name -- {self.hash_name} -- Unknown")

        return hash_value


    def get_task_ids(self, x):

        if self.split_task == "balanced":
            assert self._nb_tasks is not None
            nb_examples = len(x)
            task_ids = np.ones(nb_examples) * (self._nb_tasks - 1)
            example_per_tasks = nb_examples // self._nb_tasks
            perfect_balance_task_ids = np.arange(self._nb_tasks).repeat(example_per_tasks)
            task_ids[:len(perfect_balance_task_ids)] = perfect_balance_task_ids

            # examples from len(perfect_balance_task_ids) to len(task_ids) are put into last tasks
        elif self._nb_tasks is not None:
            # we use KMeans from scikit learn to make hash coherent tasks with a fixed number of task
            # int_hash = np.array([int(hex_str, 16) for hex_str in x])
            # make in artificially 2d array for Kmeans
            # int_hash = np.array([int_hash, int_hash]).reshape(-1, 2)

            #sim_matrix = pairwise_distances(X=x, metric="hamming")
            sim_matrix = similarity_matrix(x)

            # reduce data size for clustering
            pca = PCA(n_components=2)
            reduc_data = pca.fit_transform(sim_matrix)

            # we use kmeans from scikit learn to create coherent clusters
            task_ids = KMeans(n_clusters=self._nb_tasks).fit_predict(reduc_data)
            # task_ids = kmeans.predict(int_hash)
        else:
            # we use MeanShift from scikit learn to automatically set the number of task
            # and make hash coherent tasks
            # int_hash = np.array([int(hex_str, 16) for hex_str in x])
            # make in artificially 2d array
            # int_hash = np.array([int_hash, int_hash]).reshape(-1, 2)
            # sim_matrix = pairwise_distances(X=x, metric="hamming")
            sim_matrix = similarity_matrix(x)

            # reduce data size for clustering
            pca = PCA(n_components=2)
            reduc_data = pca.fit_transform(sim_matrix)
            task_ids = MeanShift(bandwidth=2, bin_seeding=True).fit_predict(reduc_data)
            self._nb_tasks = len(np.unique(task_ids))
            if not self._nb_tasks > 1:
                AssertionError("The number of task is expected to be more than one.")
            # task_ids = clustering.predict(int_hash)

        return task_ids

    def get_list_hash_ids(self, x):
        # multithread hash evaluation without changing list order
        with Pool(min(8, cpu_count())) as p:
            list_hash = p.map(self.hash_func, list(x))
        return list_hash

    def generate_task_ids(self, cl_dataset):
        x, y, _ = cl_dataset.get_data()

        if self.filename_hash_indexes is not None and os.path.exists(self.filename_hash_indexes):
            print(f"Loading previously saved sorted indexes ({self.filename_hash_indexes}).")
            tuple_indexes_hash = np.load(self.filename_hash_indexes, allow_pickle=True)
            sort_indexes, list_hash = tuple_indexes_hash[0].astype(int), tuple_indexes_hash[1]
            assert len(sort_indexes) == len(list_hash), print(
                f"sort_indexes {len(sort_indexes)} - list_hash {len(list_hash)}")
        else:
            list_hash = self.get_list_hash_ids(x)
            sort_indexes = sort_hash(list_hash)

            # save eventually sort_indexes for later use and gain of time
            if self.filename_hash_indexes is not None:
                np.save(self.filename_hash_indexes, [sort_indexes, list_hash], allow_pickle=True)

        x = x[sort_indexes]
        y = y[sort_indexes]
        ordered_hash = np.array(list_hash)[sort_indexes]
        task_ids = self.get_task_ids(ordered_hash)
        if not len(task_ids) == len(y):
            print(f"task_ids {len(task_ids)} - y {len(y)} should be equal")

        return x, y, task_ids

    # nothing to do in the setup function
    def _setup(self, nb_tasks: int) -> int:
        return nb_tasks
