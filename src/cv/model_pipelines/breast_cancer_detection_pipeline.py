from src.cv.model_pipelines.dl_base_pipeline import CNNTrainingPipeline
from src.cv.pytorch.models.resnet import VanillaResnet
from typing import Dict, Optional, List
import numpy as np
from torch.utils.data import Dataset
import torch
import torch.nn as nn
import torch.optim as optim
import sys


class BreastCancerTrainingPipeline(CNNTrainingPipeline):

    # Doctring generated by chatGPT
    """
    A pipeline to train BreastCancerDetection model on a facial keypoint detection dataset.
    Parameters:
        dataset (Dataset): The dataset to be used for training and validation.
        model_training_config (Dict): A dictionary containing the training configuration such as number of epochs, batch size etc.
        model_data_config (Dict): A dictionary containing the data configuration such as data preprocessing options.
        model_initialization_params (Dict): A dictionary containing the parameters to initialize the model.
        load_model_from_path (str, optional): The path to the checkpoint file containing a pre-trained model to be loaded.

    Methods:
        initialize_optimization_parameters(lr: float) -> Dict: Initializes the optimizer and criterion to be used for training.
        _initialize_model(device: str, model_params: Dict) -> torch.nn.Module: Initializes the model with the given parameters.
        _fit_model() -> float: Trains the model on the training data and returns the training loss.
        _validate_model() -> float: Validates the model on the validation data and returns the validation loss.
        get_predictions(test_dataloader: DataLoader, device: str) -> List: Makes predictions on the test data and returns a list of predictions.
    """

    def __init__(self, 
        dataset: Dataset, 
        model_training_config: Dict, 
        model_data_config: Dict, 
        model_initialization_params: Dict,
        load_model_from_path: Optional[str] = None
    ):
          super().__init__(
            dataset=dataset, 
            model_training_config=model_training_config, 
            model_data_config=model_data_config, 
            model_initialization_params=model_initialization_params,
            load_model_from_path=load_model_from_path
        )

    def initialize_optimization_parameters(self, lr=0.0005, weights = None) -> Dict:
        criterion = nn.nn.BCEWithLogitsLoss(weights)
        optimizer = optim.Adam(
            self.model.parameters(), lr=lr
        )
        
        return optimizer, criterion

    def _initialize_model(self, device, model_params):

        other_required_keys = dict(
            dropout_threshold=0.09,
            num_linear_layers=1,
            output_channels=None,
            add_dropout_to_linear_layers=True
        
        )
        wraping_layers_map = {}
        for key, value in other_required_keys.items():
            wraping_layers_map[key] = model_params.pop(key, value)

        model = VanillaResnet(**model_params).to(device)
        model.wrap_up_network(**wraping_layers_map)
        return model

    def _generate_train_validation_indices(self):
        columns_required = ["image_id", "patient_id", "laterality", "cancer"]
        test_pct = 0.3
        if self.model_data_config.validation_size:
            df = self.dataset.image_labels[columns_required]

        df["patient_id"] = df.apply(
            lambda row: f"{row['patient_id']}_{row['laterality']}", axis=1).drop(columns=["laterality"])

        num_cancer_val_ids = round(df[df.cancer == 1]["patient_id"].nunique() * test_pct)
        num_ncancer_val_ids = round(df[df.cancer == 0]["patient_id"].nunique() * test_pct)
        cancer_val_ids = list(np.random.choice(df[df.cancer == 1]["patient_id"].unique(), num_cancer_val_ids))
        num_ncancer_val_ids = list(np.random.choice(df[df.cancer == 0]["patient_id"].unique(), num_ncancer_val_ids))

        val_ids = cancer_val_ids + num_ncancer_val_ids
        train_ids = [patient_id for patient_id in df.patient_id.unique() if patient_id not in val_ids]
        val_indices = df[df.patient_id.isin(val_ids)].index
        train_indices = df[df.patient_id.isin(train_ids)].index

        return train_indices, val_indices

    def _fit_model(self):
        self.model.train()
        batch_training_loss: float = 0

        for idx, data in enumerate(self.train_dataloader):
            image = data["image"]
            label = data["label"]
            # convert variables to floats for regression loss
            label = label.type(torch.FloatTensor)
            image = image.type(torch.FloatTensor)
    #         # inp is shape (N, C, H, W)
            image = image.reshape(image.shape[0], image.shape[-1], image.shape[1], image.shape[2])
            image = image.to(self.model_training_config.device)
            label = label.to(self.model_training_config.device)
            self.optimizer.zero_grad()
            outputs = self.model(image)
            loss = self.criterion(
                outputs, 
                label.reshape(label.shape[0], 1)
            )

            batch_training_loss += loss.item()
            loss.backward()
            self.optimizer.step()

        train_loss = batch_training_loss/ (idx +1)
        return train_loss

    def _validate_model(self):
        batch_validation_loss: float = 0
        self.model.eval()
        with torch.no_grad():

            for idx, data in enumerate(self._validation_dataloader):
                image = data["image"]
                label = data["label"]
                # convert variables to floats for regression loss
                label = label.type(torch.FloatTensor)
                image = image.type(torch.FloatTensor)
                image = image.reshape(image.shape[0], image.shape[-1], image.shape[1], image.shape[2])
                image = image.to(self.model_training_config.device)
                label = label.to(self.model_training_config.device)
                outputs = self.model(image)
                loss = self.criterion(
                    outputs, 
                    label.reshape(label.shape[0], 1)
                )
                batch_validation_loss += loss.item()

        validation_loss = batch_validation_loss/idx + 1
        if min_validation_loss > validation_loss:
            min_validation_loss = validation_loss
            self._final_trained_model = self.model
        return validation_loss,

    def get_predictions(self, test_dataloader: List) -> List:

        model = self.best_model
        model.eval()

        output_keyp = []
        for data in test_dataloader:
            image = data.type(torch.FloatTensor)
            image = image.type(torch.FloatTensor)
            image = image.reshape(image.shape[0], image.shape[-1], image.shape[1], image.shape[2])
            image = image.to(self.model_training_config.device)
            outputs = model(image)
            if self.model_training_config.device == "cuda":
                outputs = outputs.cpu().data.numpy()
            output_keyp.append(outputs)

        return [preds.cpu().detach().numpy() for batch in output_keyp for preds in batch]

    def generate_test_dataloader_from_dataset(self, dataset: Dataset) -> List:

        dataloader = []
        index = 0
        dataset_finished = False
        current_end = self.model_training_config.batch_size
        while(current_end <= len(dataset)):
            dataloader.append(torch.from_numpy(np.array(
                [dataset[j]["image"] for j in range(index, current_end)]
            )))
            if dataset_finished:
                break
            index = current_end
            current_end += self.model_training_config.batch_size
            if current_end > len(dataset):
                current_end = len(dataset)
                dataset_finished = True

        return dataloader

    