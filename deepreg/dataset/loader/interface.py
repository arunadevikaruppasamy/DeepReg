"""
Interface between the data loaders and file loaders
"""
import logging
from abc import ABC

import numpy as np
import tensorflow as tf

from deepreg.dataset.preprocess import AffineTransformation3D, resize_inputs
from deepreg.dataset.util import get_label_indices


class DataLoader:
    """
    loads data to feed to model
    """

    def __init__(
        self,
        labeled: (bool, None),
        num_indices: (int, None),
        sample_label: (str, None),
        seed: (int, None) = None,
    ):
        """
        :param labeled: bool corresponding to labels provided or omitted
        :param num_indices : int
        :param sample_label : (str, None)
        :param seed : (int, None), optional
        """
        self.labeled = labeled
        self.num_indices = num_indices  # number of indices to identify a sample
        self.sample_label = sample_label
        self.seed = seed  # used for sampling

    @property
    def moving_image_shape(self) -> tuple:
        """
        needs to be defined by user
        """
        raise NotImplementedError

    @property
    def fixed_image_shape(self) -> tuple:
        """
        needs to be defined by user
        """
        raise NotImplementedError

    @property
    def num_samples(self) -> int:
        """
        Return the number of samples in the dataset for one epoch
        :return:
        """
        raise NotImplementedError

    def get_dataset(self) -> tf.data.Dataset:
        """
        defined in GeneratorDataLoader
        """
        raise NotImplementedError

    def get_dataset_and_preprocess(
        self,
        training: bool,
        batch_size: int,
        repeat: bool,
        shuffle_buffer_num_batch: int,
    ) -> tf.data.Dataset:
        """
        :param training: bool, indicating if it's training or not
        :param batch_size: int, size of mini batch
        :param repeat: bool, indicating if we need to repeat the dataset
        :param shuffle_buffer_num_batch: int, when shuffling, the shuffle_buffer_size = batch_size * shuffle_buffer_num_batch

        :returns dataset:
        """

        dataset = self.get_dataset()

        # resize
        dataset = dataset.map(
            lambda x: resize_inputs(
                inputs=x,
                moving_image_size=self.moving_image_shape,
                fixed_image_size=self.fixed_image_shape,
            ),
            num_parallel_calls=tf.data.experimental.AUTOTUNE,
        )

        # shuffle / repeat / batch / preprocess
        if training and shuffle_buffer_num_batch > 0:
            dataset = dataset.shuffle(
                buffer_size=batch_size * shuffle_buffer_num_batch,
                reshuffle_each_iteration=True,
            )
        if repeat:
            dataset = dataset.repeat()
        dataset = dataset.batch(batch_size=batch_size, drop_remainder=training)
        dataset = dataset.prefetch(tf.data.experimental.AUTOTUNE)
        if training:
            # TODO add cropping, but crop first or rotation first?
            affine_transform = AffineTransformation3D(
                moving_image_size=self.moving_image_shape,
                fixed_image_size=self.fixed_image_shape,
                batch_size=batch_size,
            )
            dataset = dataset.map(
                affine_transform.transform,
                num_parallel_calls=tf.data.experimental.AUTOTUNE,
            )
        return dataset

    def close(self):
        pass


class AbstractPairedDataLoader(DataLoader, ABC):
    """
    Abstract loader for paried data independent of file format
    """

    def __init__(
        self,
        moving_image_shape: (list, tuple),
        fixed_image_shape: (list, tuple),
        **kwargs,
    ):
        """
        num_indices = 2 corresponding to (image_index, label_index)
        :param moving_image_shape: (width, height, depth)
        :param fixed_image_shape:  (width, height, depth)
        """
        super(AbstractPairedDataLoader, self).__init__(num_indices=2, **kwargs)
        if len(moving_image_shape) != 3 or len(fixed_image_shape) != 3:
            raise ValueError(
                "moving_image_shape and fixed_image_shape have to be length of three,"
                "corresponding to (width, height, depth)"
            )
        self._moving_image_shape = tuple(moving_image_shape)
        self._fixed_image_shape = tuple(fixed_image_shape)
        self.num_images = None

    @property
    def moving_image_shape(self) -> tuple:
        """
        Return the moving image shape.
        :return: shape of moving image
        """
        return self._moving_image_shape

    @property
    def fixed_image_shape(self) -> tuple:
        """
        Return the fixed image shape.
        :return: shape of fixed image
        """
        return self._fixed_image_shape

    @property
    def num_samples(self) -> int:
        """
        Return the number of samples in the dataset for one epoch.
        :return: number of images
        """
        return self.num_images


class AbstractUnpairedDataLoader(DataLoader, ABC):
    """
    Abstract loader for unparied data independent of file format
    """

    def __init__(self, image_shape: (list, tuple), **kwargs):
        """
        - image_shape is the shape of images fed into dataset,
        it is assumed to be 3d, [dim1, dim2, dim3]
          moving_image_shape = fixed_image_shape = image_shape
        """
        super(AbstractUnpairedDataLoader, self).__init__(num_indices=3, **kwargs)
        if len(image_shape) != 3:
            raise ValueError(
                "image_shape has to be length of three,"
                "corresponding to (width, height, depth)"
            )
        self.image_shape = tuple(image_shape)
        self._num_samples = None

    @property
    def moving_image_shape(self) -> tuple:
        return self.image_shape

    @property
    def fixed_image_shape(self) -> tuple:
        return self.image_shape

    @property
    def num_samples(self) -> int:
        return self._num_samples


class GeneratorDataLoader(DataLoader, ABC):
    """
    handles loading of samples by implementing get_dataset from DataLoader
    """

    def __init__(self, **kwargs):
        super(GeneratorDataLoader, self).__init__(**kwargs)
        self.loader_moving_image = None
        self.loader_fixed_image = None
        self.loader_moving_label = None
        self.loader_fixed_label = None

    def get_dataset(self):
        """
        returns a dataset from the generator
        """
        if self.labeled:
            return tf.data.Dataset.from_generator(
                generator=self.data_generator,
                output_types=dict(
                    moving_image=tf.float32,
                    fixed_image=tf.float32,
                    moving_label=tf.float32,
                    fixed_label=tf.float32,
                    indices=tf.float32,
                ),
                output_shapes=dict(
                    moving_image=tf.TensorShape([None, None, None]),
                    fixed_image=tf.TensorShape([None, None, None]),
                    moving_label=tf.TensorShape([None, None, None]),
                    fixed_label=tf.TensorShape([None, None, None]),
                    indices=self.num_indices,
                ),
            )
        else:
            return tf.data.Dataset.from_generator(
                generator=self.data_generator,
                output_types=dict(
                    moving_image=tf.float32, fixed_image=tf.float32, indices=tf.float32
                ),
                output_shapes=dict(
                    moving_image=tf.TensorShape([None, None, None]),
                    fixed_image=tf.TensorShape([None, None, None]),
                    indices=self.num_indices,
                ),
            )

    def data_generator(self):
        """
        yeild samples of data to feed model
        """
        for (moving_index, fixed_index, image_indices) in self.sample_index_generator():
            moving_image = self.loader_moving_image.get_data(index=moving_index) / 255.0
            fixed_image = self.loader_fixed_image.get_data(index=fixed_index) / 255.0
            moving_label = (
                self.loader_moving_label.get_data(index=moving_index)
                if self.labeled
                else None
            )
            fixed_label = (
                self.loader_fixed_label.get_data(index=fixed_index)
                if self.labeled
                else None
            )

            for sample in self.sample_image_label(
                moving_image=moving_image,
                fixed_image=fixed_image,
                moving_label=moving_label,
                fixed_label=fixed_label,
                image_indices=image_indices,
            ):
                yield sample

    def sample_index_generator(self):
        """
        this method needs to be defined by the data loaders that are implemented
        it needs to yield the sample indexes
        only used in data_generator
        """
        raise NotImplementedError

    @staticmethod
    def validate_images_and_labels(
        moving_image: np.ndarray,
        fixed_image: np.ndarray,
        moving_label: (np.ndarray, None),
        fixed_label: (np.ndarray, None),
        image_indices: list,
    ):
        """
        check that all file names match according to naming convention
        only used in sample_image_label
        :param moving_image: np.ndarray of shape (m_dim1, m_dim2, m_dim3)
        :param fixed_image: np.ndarray of shape (f_dim1, f_dim2, f_dim3)
        :param moving_label: np.ndarray of shape (m_dim1, m_dim2, m_dim3) or (m_dim1, m_dim2, m_dim3, num_labels) or None
        :param fixed_label: np.ndarray of shape (f_dim1, f_dim2, f_dim3) or (f_dim1, f_dim2, f_dim3, num_labels) or None
        :param image_indices: list
        """
        # images should never be None, and labels should all be non-None or None
        if moving_image is None or fixed_image is None:
            raise ValueError("moving image and fixed image must not be None")
        if (moving_label is None) != (fixed_label is None):
            raise ValueError(
                "moving label and fixed label must be both None or non-None"
            )
        # image and label's values should be between [0, 1]
        for arr, name in zip(
            [moving_image, fixed_image, moving_label, fixed_label],
            ["moving_image", "fixed_image", "moving_label", "fixed_label"],
        ):
            if arr is None:
                continue
            if np.min(arr) < 0 or np.max(arr) > 1:
                raise ValueError(
                    f"Sample {image_indices}'s {name} has normalized value outside of [0,1]."
                    f"Images are assumed to have values between [0, 255] after loading"
                    f"and labels are assumed to be binary"
                )
        # images should be 3D arrays
        for arr, name in zip(
            [moving_image, fixed_image], ["moving_image", "fixed_image"]
        ):
            if len(arr.shape) != 3:
                raise ValueError(
                    f"Sample {image_indices}'s {name}'s shape should have dimension of 3. "
                    f"Got {arr.shape}."
                )
        # when data are labeled
        if moving_label is not None:
            # labels should be 3D or 4D arrays
            for arr, name in zip(
                [moving_label, fixed_label], ["moving_label", "fixed_label"]
            ):
                if len(arr.shape) not in [3, 4]:
                    raise ValueError(
                        f"Sample {image_indices}'s {name}'s shape should have dimension of 3 or 4. "
                        f"Got {arr.shape}."
                    )
            # image and label is better to have the same shape
            if moving_image.shape[:3] != moving_label.shape[:3]:
                logging.warning(
                    f"Sample {image_indices}'s moving image and label have different shapes. "
                    f"moving_image.shape = {moving_image.shape}, moving_label.shape = {moving_label.shape}"
                )
            if fixed_image.shape[:3] != fixed_label.shape[:3]:
                logging.warning(
                    f"Sample {image_indices}'s fixed image and label have different shapes. "
                    f"fixed_image.shape = {fixed_image.shape}, fixed_label.shape = {fixed_label.shape}"
                )
            # number of labels for fixed and fixed images should be the same
            num_labels_moving = (
                1 if len(moving_label.shape) == 3 else moving_label.shape[-1]
            )
            num_labels_fixed = (
                1 if len(fixed_label.shape) == 3 else fixed_label.shape[-1]
            )
            if num_labels_moving != num_labels_fixed:
                raise ValueError(
                    f"Sample {image_indices}'s moving image and fixed image have different numbers of labels."
                    f"moving: {num_labels_moving}, fixed: {num_labels_fixed}"
                )

    def sample_image_label(
        self,
        moving_image: np.ndarray,
        fixed_image: np.ndarray,
        moving_label: (np.ndarray, None),
        fixed_label: (np.ndarray, None),
        image_indices: list,
    ):
        """
        sample the image labels
        only used in data_generator
        :param moving_image : np.ndarray
        :param fixed_image : np.ndarray
        :param moving_label : (np.ndarray, None)
        :param fixed_label : (np.ndarray, None)
        :param image_indices : list
        """
        self.validate_images_and_labels(
            moving_image, fixed_image, moving_label, fixed_label, image_indices
        )
        # unlabeled
        if moving_label is None:
            label_index = -1  # means no label
            indices = np.asarray(image_indices + [label_index], dtype=np.float32)
            yield dict(
                moving_image=moving_image, fixed_image=fixed_image, indices=indices
            )
        else:
            # labeled
            if len(moving_label.shape) == 4:  # multiple labels
                label_indices = get_label_indices(
                    moving_label.shape[3], self.sample_label
                )
                for label_index in label_indices:
                    indices = np.asarray(
                        image_indices + [label_index], dtype=np.float32
                    )
                    yield dict(
                        moving_image=moving_image,
                        fixed_image=fixed_image,
                        indices=indices,
                        moving_label=moving_label[..., label_index],
                        fixed_label=fixed_label[..., label_index],
                    )
            else:  # only one label
                label_index = 0
                indices = np.asarray(image_indices + [label_index], dtype=np.float32)
                yield dict(
                    moving_image=moving_image,
                    fixed_image=fixed_image,
                    moving_label=moving_label,
                    fixed_label=fixed_label,
                    indices=indices,
                )


class ConcatenatedDataLoader(DataLoader):
    """
    Given multiple data_dir_paths, build a data_loader for each path,
    and concatenate all data loaders
    """

    def __init__(self, data_loaders):
        super(ConcatenatedDataLoader, self).__init__(
            labeled=None, num_indices=None, sample_label=None, seed=None
        )
        assert len(data_loaders) > 0
        self.loaders = data_loaders

    @property
    def moving_image_shape(self) -> tuple:
        return self.loaders[0].moving_image_shape

    @property
    def fixed_image_shape(self) -> tuple:
        return self.loaders[0].fixed_image_shape

    @property
    def num_samples(self) -> int:
        return sum([loader.num_samples for loader in self.loaders])

    def get_dataset(self):
        for i, loader in enumerate(self.loaders):
            if i == 0:
                dataset = loader.get_dataset()
            else:
                dataset = dataset.concatenate(loader.get_dataset())
        return dataset

    def close(self):
        for loader in self.loaders:
            loader.close()


class FileLoader:
    """
    contians funcitons which need to be defined for different file formats
    """

    def __init__(self, dir_path: str, name: str, grouped: bool):
        """
        :param dir_path: path to the directory of the data set
        :param name: name is used to identify the subdirectories or file names
        :param grouped: true if the data is grouped
        """
        self.dir_path = dir_path
        self.name = name
        self.grouped = grouped
        if grouped:
            self.group_ids = None
            self.group_sample_dict = None

    def get_data(self, index: (int, tuple)):
        """
        return the data corresponding to the given index
        :param index:
        :return:
        """
        raise NotImplementedError

    def get_data_ids(self):
        """
        return the unique IDs of the data in this data set
        this function is used to verify the consistency between
        images and label, moving and fixed
        :return:
        """
        raise NotImplementedError

    def get_num_images(self) -> int:
        """
        return the number of images in this data set
        :return:
        """
        raise NotImplementedError

    def set_group_structure(self):
        """
        save variables to store the structure of the groups
        set group_ids and group_sample_dict
        :return:
        """
        raise NotImplementedError

    def get_num_groups(self) -> int:
        assert self.grouped
        return len(self.group_ids)

    def get_num_images_per_group(self) -> list:
        """
        calculate the number of images in each group
        each group must have at least one image
        """
        assert self.grouped
        num_images_per_group = [len(self.group_sample_dict[g]) for g in self.group_ids]
        if min(num_images_per_group) == 0:
            group_ids = [
                g for g in self.group_ids if len(self.group_sample_dict[g]) == 0
            ]
            raise ValueError(f"Groups of ID {group_ids} are empty.")
        return num_images_per_group

    def close(self):
        """close opened file handles"""
        raise NotImplementedError
