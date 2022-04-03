from typing import Optional, Any

from fastapi import UploadFile
from sqlalchemy.orm import Session
from app.models import models
from app.crud import csv as csv_crud
from app.crud import experiment as experiment_crud
from app.crud import subject as subject_crud
from datetime import datetime, timedelta
import os
from app.schemas.csv import CSVCopy, CSVFilters
from app.schemas.preproccessing import ICAMethod, ICAExclude
from app.schemas.feature_extraction import FeaturePost
import json
import numpy as np
import pandas as pd
from mne.io import RawArray
import mne
import base64
import app.crud.training as training_crud
from app.schemas.epoch import EpochPlot, EpochAverage, EpochCompare, EpochActivity
import matplotlib.pyplot as plt
import math



def get_csv_by_id(db: Session, csv_id: int) -> Optional[models.CSV]:
    csv = csv_crud.find_by_id(db, csv_id)
    return csv


def get_all_csv_preproccessing(db: Session, csv_id: int) -> Optional[list[models.Preproccessing]]:
    csv = csv_crud.find_by_id(db, csv_id)

    if csv is None:
        return None

    return csv.preproccessing_list


def get_all_csv_features(db: Session, csv_id: int) -> Optional[list[models.FeatureExtraction]]:
    csv = csv_crud.find_by_id(db, csv_id)

    if csv is None:
        return None

    return csv.feature_extractions

def get_all_csv_experiment(db: Session, experiment_id: int) -> Optional[list[models.Experiment]]:
    e = experiment_crud.find_by_id(db, experiment_id)
    if e is None:
        return None
    return e.csvs


def create_csv(db: Session, name: str, subject_id: int, experiment_id: int, time_correction: float, files: list[UploadFile]) -> Optional[models.CSV]:
    exp = experiment_crud.find_by_id(db, experiment_id)
    subject = subject_crud.find_by_id(db, subject_id)
    if exp is None or subject is None:
        return None

    object = {
        "dataInput": [],
        "timestamp": [],
        "stimuli": []
    }

    for file in files:
        aux = json.loads(file.file.read())
        object["dataInput"].extend(aux["dataInput"])
        object["timestamp"].extend(aux["timestamp"])
        object["stimuli"].extend(aux["stimuli"])

        for stimulus in object["stimuli"]:
            cont = 0
            for label in exp.labels:
                if stimulus[0][0] != int(label.label):
                    cont += 1
            if cont == len(exp.labels):
                return None

    name_file = generate_name_csv(db)
    df = None
    if exp.device.type == 'eeg_headset':
        df = create_csv_eegheadset(object, exp, name_file, time_correction)

    rawdata = load_raw(df, exp)
    events = mne.find_events(rawdata, shortest_event=1)
    event_id = {}
    for label in exp.labels:
        event_id[label.description] = int(label.label)


    epochs = mne.Epochs(rawdata, events=events, event_id=event_id, tmin=exp.epoch_start, tmax=exp.epoch_end)

    str_epoch_list = str(epochs).split(',')
    str_epoch = str_epoch_list[len(str_epoch_list)-1].replace('\n', '').replace('\'', '')
    str_epoch = str_epoch[1:]
    str_epoch = str_epoch[:-1]

    db_csv = models.CSV(name=name,
                        subject_name=subject.name + ' ' + subject.surname,
                        type='original',
                        experiment_id=experiment_id,
                        path=name_file,
                        date=name_file[12:31],
                        duraction=df.shape[0]/exp.device.sample_rate,
                        events=len(events),
                        epochs=str_epoch)

    subject.total_experiments_performed = subject.total_experiments_performed + 1
    subject_crud.save(db, subject)

    return csv_crud.save(db, db_csv)


def delete_csv(db: Session, csv_id: int) -> bool:
    csv = csv_crud.find_by_id(db, csv_id)

    if csv is None:
        return False
    try:
        os.remove(csv.path)
    except FileNotFoundError:
        pass
    for training in csv.trainings:
        if len(training.csvs) == 1:
            training_crud.delete(db, training)

    csv_crud.delete(db, csv)
    return True


def csv_copy(db: Session, csv_id: int, csv_copy: CSVCopy) -> Optional[models.CSV]:
    csv_original = csv_crud.find_by_id(db, csv_id)

    if csv_original is None:
        return None

    try:
        file = pd.read_csv(csv_original.path)
    except FileNotFoundError:
        return None

    name_file = generate_name_csv(db)
    file.to_csv(name_file, index=False)

    db_csv = models.CSV(name=csv_copy.name,
                        subject_name=csv_original.subject_name,
                        type='copied',
                        experiment_id=csv_original.experiment_id,
                        path=name_file,
                        date=name_file[12:31],
                        duraction=csv_original.duraction,
                        epochs=csv_original.epochs,
                        events=csv_original.events)

    for x in csv_original.preproccessing_list:
        db_preproccessing = models.Preproccessing(
            position=x.position,
            preproccessing=x.preproccessing,
            csv_id=db_csv.id,
            description=x.description)
        db_csv.preproccessing_list.append(db_preproccessing)

    return csv_crud.save(db, db_csv)


def change_name(db: Session, csv_id: int, csv_copy: CSVCopy) -> Optional[models.CSV]:
    csv = csv_crud.find_by_id(db, csv_id)

    if csv is None:
        return None

    csv.name = csv_copy.name
    return csv_crud.save(db, csv)


def apply_preproccessing(db: Session, csv_filters: CSVFilters):
    exp = None
    for csv_id in csv_filters.csvs:
        csv = csv_crud.find_by_id(db, csv_id)
        if csv is not None:
            if len(csv.feature_extractions) > 0:
                return "In some csv have already applied feature extraction. Please, unselect"
            if exp is None:
                exp = experiment_crud.find_by_id(db, csv.experiment_id)

            df = pd.read_csv(csv.path)
            rawdata = load_raw(df, exp)


            if rawdata is not None:
                for prep in csv_filters.preproccessings:
                    if prep.__class__.__name__ == 'CSVBandpass':
                        try:
                            rawdata = apply_bandpass(prep, rawdata, csv)

                        except ValueError:
                            return "Check frequency values"
                        except np.linalg.LinAlgError:
                            return "Array must not contain infs or NaNs"
                    elif prep.__class__.__name__ == 'CSVNotch':
                        try:
                            rawdata = apply_notch(prep, rawdata, csv)
                        except ValueError:
                            return "Check frequency values"
                        except np.linalg.LinAlgError:
                            return "Array must not contain infs or NaNs"
                    elif prep.__class__.__name__ == 'CSVDownsampling':
                        try:
                            rawdata = apply_downsampling(prep, rawdata, csv)
                        except ValueError:
                            return "Check frequency values"
                        except np.linalg.LinAlgError:
                            return "Array must not contain infs or NaNs"

                os.remove(csv.path)
                csv.path = generate_name_csv(db)
                csv.date = csv.path[12:31]
                csv.type = 'prep'

                ch_names = []
                for x in exp.device.channels:
                    ch_names.append(x.channel.name)

                data = convert_to_df(rawdata, ch_names)

                data.to_csv(csv.path, index=False)
                csv.duraction = data.shape[0]/exp.device.sample_rate
                csv_crud.save(db, csv)


def load_raw(df, experiment):

    if experiment.device.type == 'eeg_headset':
        if "Timestamp" in df.columns:
            del df['Timestamp']
        ch_names = list(df.columns)[0:experiment.device.channels_count] + ['Stim']
        ch_types = ['eeg'] * experiment.device.channels_count + ['stim']

        ch_ind = []
        for x in range(0, experiment.device.channels_count):
            ch_ind.append(x)

        data = df.values[:, ch_ind + [experiment.device.channels_count]].T

        info = mne.create_info(ch_names=ch_names, ch_types=ch_types, sfreq=experiment.device.sample_rate)
        raw = RawArray(data=data, info=info)
        raw.set_montage('standard_1020')
        return raw

    return None


def apply_feature(db: Session, feature_post: FeaturePost):
    exp = None
    new_df = None
    for csv_id in feature_post.csvs:
        csv = csv_crud.find_by_id(db, csv_id)
        if csv is None:
            break
        if exp is None:
            exp = experiment_crud.find_by_id(db, csv.experiment_id)

        df = pd.read_csv(csv.path)

        if feature_post.feature == 'mean':
            new_df = apply_mean(exp, df)
            db_f = models.FeatureExtraction(
                csv_id=csv.id,
                feature_extraction="Mean")
            csv.feature_extractions.append(db_f)

        elif feature_post.feature == 'variance':
            new_df = apply_variance(exp, df)
            db_f = models.FeatureExtraction(
                csv_id=csv.id,
                feature_extraction="Variance")
            csv.feature_extractions.append(db_f)

        elif feature_post.feature == 'deviation':
            new_df = apply_standard_deviation(exp, df)
            db_f = models.FeatureExtraction(
                csv_id=csv.id,
                feature_extraction="Standard Deviation")
            csv.feature_extractions.append(db_f)

        elif feature_post.feature == 'psd':
            new_df = apply_psd(exp, df)
            db_f = models.FeatureExtraction(
                csv_id=csv.id,
                feature_extraction="Power Spectral Density")
            csv.feature_extractions.append(db_f)

        name_file = generate_name_csv(db)

        os.remove(csv.path)
        csv.path = generate_name_csv(db)
        csv.date = csv.path[12:31]
        csv.duraction = 0

        if csv.type == 'prep':
            csv.type = 'prep | feature'
        else:
            csv.type = 'feature'

        new_df.to_csv(name_file, index=False)
        csv_crud.save(db, csv)


def generate_name_csv(db: Session):
    now = datetime.now()
    name_file = "csvs/record_{}.csv".format(now.strftime("%d-%m-%Y-%H-%M-%S"))
    while csv_crud.find_by_path(db, name_file):
        now = datetime.now() + timedelta(seconds=1)
        name_file = "csvs/record_{}.csv".format(now.strftime("%d-%m-%Y-%H-%M-%S"))

    return name_file


def generate_name_tmp():
    now = datetime.now()
    return "tmp/record_{}.png".format(now.strftime("%d-%m-%Y-%H-%M-%S"))


def create_csv_eegheadset(obj: Any, exp: models.Experiment, name_file: str, time_correction: float):
    ch_names = []
    for x in exp.device.channels:
        ch_names.append(x.channel.name)

    obj["dataInput"] = np.concatenate(obj["dataInput"], axis=0)
    if len(ch_names) > 8:
        obj["dataInput"] = obj["dataInput"][:, :8]

    obj["timestamp"] = np.array(obj["timestamp"]) + time_correction
    obj["dataInput"] = np.c_[obj["timestamp"], obj["dataInput"]]
    data = pd.DataFrame(data=obj["dataInput"], columns=['Timestamp'] + ch_names)

    if len(obj["stimuli"]) != 0:
        data.loc[:, 'Stimulus'] = 0
        for estim in obj["stimuli"]:
            abs = np.abs(estim[1] - obj["timestamp"])
            ix = np.argmin(abs)
            data.loc[ix, 'Stimulus'] = estim[0][0]

    data.to_csv(name_file, index=False)

    return data


def apply_bandpass(prep, rawdata, new_csv):
    l_freq = None
    h_freq = None
    text = ''

    if prep.low_freq != '':
        l_freq = float(prep.low_freq)
        text = text + 'Low Frequency: ' + prep.low_freq + 'Hz '

    if prep.high_freq != '':
        h_freq = float(prep.high_freq)
        text = text + 'High Frequency: ' + prep.high_freq + 'Hz '

    if prep.filter_method == 'fir':
        db_preproccessing = models.Preproccessing(
                                position=len(new_csv.preproccessing_list) + 1,
                                preproccessing='Bandpass',
                                csv_id=new_csv.id,
                                description='Method: FIR, ' + 'Phase: ' + prep.phase + ', ' + text)
        new_csv.preproccessing_list.append(db_preproccessing)

        return rawdata.copy().filter(l_freq=l_freq, h_freq=h_freq,
                                      method='fir', fir_design='firwin', phase=prep.phase)

    elif prep.filter_method == 'iir':
        if prep.order == '1':
            ordinal = 'st'
        elif prep.order == '2':
            ordinal = 'nd'
        else:
             ordinal = 'th'

        db_preproccessing = models.Preproccessing(
                                position=len(new_csv.preproccessing_list) + 1,
                                preproccessing='Bandpass',
                                csv_id=new_csv.id,
                                description='Method: IIR, ' + prep.order + ordinal + '-order Butterworth filter, ' + text)
        new_csv.preproccessing_list.append(db_preproccessing)

        iir_params = dict(order=int(prep.order), ftype='butter')
        return rawdata.copy().filter(l_freq=l_freq, h_freq=h_freq,
                                      method='iir', iir_params=iir_params)


def apply_notch(prep, rawdata, new_csv):

    if prep.filter_method == 'fir':
        db_preproccessing = models.Preproccessing(
                                position=len(new_csv.preproccessing_list) + 1,
                                preproccessing='Notch',
                                csv_id=new_csv.id,
                                description='Method: FIR, ' + 'Phase: ' + prep.phase + ', ' + 'Frequency: ' + prep.freq + 'Hz')
        new_csv.preproccessing_list.append(db_preproccessing)

        return rawdata.copy().notch_filter(freqs=float(prep.freq), method='fir', fir_design='firwin', phase=prep.phase)

    elif prep.filter_method == 'iir':
        if prep.order == '1':
            ordinal = 'st'
        elif prep.order == '2':
            ordinal = 'nd'
        else:
             ordinal = 'th'

        db_preproccessing = models.Preproccessing(
            position=len(new_csv.preproccessing_list) + 1,
            preproccessing='Bandpass',
            csv_id=new_csv.id,
            description='Method: IIR, ' + prep.order + ordinal + '-order Butterworth filter, ' + 'Frequency: ' + prep.freq + 'Hz')
        new_csv.preproccessing_list.append(db_preproccessing)

        iir_params = dict(order=int(prep.order), ftype='butter')
        return rawdata.copy().notch_filter(freqs=float(prep.freq), method='iir', iir_params=iir_params)


def apply_downsampling(prep, rawdata, new_csv):
    db_preproccessing = models.Preproccessing(
        position=len(new_csv.preproccessing_list) + 1,
        preproccessing='Downsampling',
        csv_id=new_csv.id,
        description='Sample rate: ' + prep.freq_downsampling + ' Hz')
    new_csv.preproccessing_list.append(db_preproccessing)

    return rawdata.copy().resample(prep.freq_downsampling, npad="auto")


def convert_to_df(rawdata, ch_names) -> pd.DataFrame:
    #scalar = pd.to_numeric(data['Stimulus'], errors='coerce', downcast='integer')
    #del data['Stimulus']
    #data['Stimulus'] = scalar
    return pd.DataFrame(data=rawdata.get_data().T, columns=ch_names + ['Stimulus'])


def plot_properties_ica(db: Session, csv_id, ica_method: ICAMethod):
    csv = csv_crud.find_by_id(db, csv_id)

    if csv is None:
        return None

    exp = experiment_crud.find_by_id(db, csv.experiment_id)
    if exp is None:
        return None

    df = pd.read_csv(csv.path)

    rawdata = load_raw(df, exp)
    fit_params = None
    if ica_method.method == 'picard':
        fit_params = dict(ortho=True, extended=True)
    elif ica_method.method == 'infomax':
        fit_params = dict(extended=True)

    ica = mne.preprocessing.ICA(random_state=97, method=ica_method.method, fit_params=fit_params)
    ica.fit(rawdata)
    shape = ica.get_components()
    picks = []
    for x in range(0, shape.shape[1]):
        picks.append(x)

    figures = ica.plot_properties(rawdata.copy(), picks=picks)
    returned = []
    for x in figures:
        name_tmp = generate_name_tmp()
        x.savefig(name_tmp)
        with open(name_tmp, 'rb') as f:
            returned.append(base64.b64encode(f.read()))
            os.remove(name_tmp)

    return returned


def plot_components_ica(db: Session, csv_id: int, ica_method: ICAMethod):
    csv = csv_crud.find_by_id(db, csv_id)

    if csv is None:
        return None

    exp = experiment_crud.find_by_id(db, csv.experiment_id)
    if exp is None:
        return None

    df = pd.read_csv(csv.path)

    rawdata = load_raw(df, exp)
    fit_params = None
    if ica_method.method == 'picard':
        fit_params = dict(ortho=True, extended=True)
    elif ica_method.method == 'infomax':
        fit_params = dict(extended=True)

    ica = mne.preprocessing.ICA(random_state=97, method=ica_method.method, fit_params=fit_params)
    ica.fit(rawdata)
    figure = ica.plot_components()

    name_tmp = generate_name_tmp()
    figure[0].savefig(name_tmp)
    shape = ica.get_components()
    with open(name_tmp, 'rb') as f:
        base64image = base64.b64encode(f.read())
    os.remove(name_tmp)
    returned = {"img": base64image, "components": shape.shape[1]}
    return returned


def components_exclude_ica(db: Session, csv_id: int, arg: ICAExclude):
    csv = csv_crud.find_by_id(db, csv_id)

    if csv is None:
        return None

    exp = experiment_crud.find_by_id(db, csv.experiment_id)
    if exp is None:
        return None

    df = pd.read_csv(csv.path)

    rawdata = load_raw(df, exp)
    fit_params = None
    if arg.method == 'picard':
        fit_params = dict(ortho=True, extended=True)
    elif arg.method == 'infomax':
        fit_params = dict(extended=True)

    ica = mne.preprocessing.ICA(random_state=97, method=arg.method, fit_params=fit_params)
    ica.fit(rawdata)

    ica.exclude = arg.components
    ica.apply(rawdata.copy())


    text = 'Components removed: '
    for x in arg.components:
        text = text + str(x) + ", "

    text = text[:-1]
    text = text[:-1]

    db_preproccessing = models.Preproccessing(
        position=len(csv.preproccessing_list) + 1,
        preproccessing='ICA',
        csv_id=csv.id,
        description=text)
    csv.preproccessing_list.append(db_preproccessing)


    ch_names = []
    for x in exp.device.channels:
        ch_names.append(x.channel.name)
    data = convert_to_df(rawdata, ch_names)

    os.remove(csv.path)
    csv.path = generate_name_csv(db)
    csv.date = csv.path[12:31]
    csv.type = 'prep'
    csv.duraction = data.shape[0]/exp.device.sample_rate

    data.to_csv(csv.path, index=False)

    csv_crud.save(db, csv)


def get_csvs_same_features(db: Session, csv_id:int)-> Optional[list[models.CSV]]:
    csv = csv_crud.find_by_id(db, csv_id)
    if csv is None:
        return None

    all_csv = csv_crud.find_all(db)
    returned = []

    for c in all_csv:
        same = True
        if len(c.feature_extractions) == len(csv.feature_extractions) and c.id != csv.id:
            i = 0
            while i < len(c.feature_extractions) and same == True:
                if c.feature_extractions[i].feature_extraction == csv.feature_extractions[i].feature_extraction:
                    i = i + 1
                else:
                    same = False
        else:
            same = False

        if same == True:
            returned.append(c)

    return returned


def apply_mean(exp, df):

    if exp.device.type == 'eeg_headset':
        if "Timestamp" in df.columns:
            del df['Timestamp']
        rawdata = load_raw(df, exp)
        epochs = get_epoch(rawdata, exp)
        data_epochs = epochs.get_data()

        array = []

        for estim in (range(len(data_epochs))):
            value_estim = 0
            row = []
            for x in (range(len(data_epochs[estim]) - 1) ):
                sum = 0
                for y in (range(len(data_epochs[estim][x]))):
                    sum = sum + data_epochs[estim][x][y]
                    if data_epochs[estim][len(data_epochs[estim]) - 1][y] != 0:
                        value_estim = data_epochs[estim][len(data_epochs[estim]) - 1][y]
                row.append(sum/len(data_epochs[estim][x]))
            row.append(value_estim)
            array.append(row)

        ch_names = []
        for x in exp.device.channels:
            ch_names.append(x.channel.name + '_mean')
        return pd.DataFrame(array, columns=ch_names + ['Stimulus'])



def apply_variance(exp, df):
    if exp.device.type == 'eeg_headset':
        if "Timestamp" in df.columns:
            del df['Timestamp']

        rawdata = load_raw(df, exp)
        epochs = get_epoch(rawdata, exp)

        data_epochs = epochs.get_data()

        array = []

        for estim in (range(len(data_epochs))):
            value_estim = 0
            row = []
            for x in (range(len(data_epochs[estim]) - 1)):

                sum = 0
                var = 0

                for y in (range(len(data_epochs[estim][x]))):
                    sum = sum + data_epochs[estim][x][y]
                    if data_epochs[estim][len(data_epochs[estim]) - 1][y] != 0:
                        value_estim = data_epochs[estim][len(data_epochs[estim]) - 1][y]
                mean = (sum / len(data_epochs[estim][x]))

                for y2 in (range(len(data_epochs[estim][x]))):
                    var = (data_epochs[estim][x][y2] - mean)**2
                row.append(var/len(data_epochs[estim][x]))

            row.append(value_estim)
            array.append(row)

        ch_names = []
        for x in exp.device.channels:
            ch_names.append(x.channel.name + '_variance')
        return pd.DataFrame(array, columns=ch_names + ['Stimulus'])


def apply_standard_deviation (exp, df):
    if exp.device.type == 'eeg_headset':
        if "Timestamp" in df.columns:
            del df['Timestamp']

        rawdata = load_raw(df, exp)
        epochs = get_epoch(rawdata, exp)

        data_epochs = epochs.get_data()

        array = []

        for estim in (range(len(data_epochs))):
            value_estim = 0
            row = []
            for x in (range(len(data_epochs[estim]) - 1)):

                sum = 0
                var = 0

                for y in (range(len(data_epochs[estim][x]))):
                    sum = sum + data_epochs[estim][x][y]
                    if data_epochs[estim][len(data_epochs[estim]) - 1][y] != 0:
                        value_estim = data_epochs[estim][len(data_epochs[estim]) - 1][y]
                mean = (sum / len(data_epochs[estim][x]))

                for y2 in (range(len(data_epochs[estim][x]))):
                    var = (data_epochs[estim][x][y2] - mean) ** 2
                row.append(math.sqrt(var / len(data_epochs[estim][x])))

            row.append(value_estim)
            array.append(row)

        ch_names = []
        for x in exp.device.channels:
            ch_names.append(x.channel.name + '_deviation_standard')
        return pd.DataFrame(array, columns=ch_names + ['Stimulus'])


def apply_psd (exp, df):

    if exp.device.type == 'eeg_headset':
        if "Timestamp" in df.columns:
            del df['Timestamp']

        rawdata = load_raw(df, exp)
        epochs = get_epoch(rawdata, exp)

        prueba = mne.time_frequency.psd_welch(epochs, n_per_seg=256, picks='eeg')
        return None



def plot_chart(db: Session, csv_id: int, beginning:int, duraction:int):

    csv = csv_crud.find_by_id(db, csv_id)

    if csv is None:
        return None

    exp = experiment_crud.find_by_id(db, csv.experiment_id)

    file = pd.read_csv(csv.path)

    values = file.iloc[int(beginning * exp.device.sample_rate): int((beginning * exp.device.sample_rate) + (duraction * exp.device.sample_rate))].transpose().values.tolist()

    if len(csv.preproccessing_list) == 0 and len(csv.feature_extractions) == 0:
        del values[0]

    returned = []


    for i in (range(len(values) - 1)):
        unidimensional = []
        for j in (range(len(values[i]))):
            dict = {"pv": values[i][j] }
            unidimensional.append(dict)
        returned.append(unidimensional)


    stimulus = []
    for i in (range(len(values[len(values)-1]))):
        if values[len(values)-1][i] != 0:
            stimulus.append({"x": i, "stim": values[len(values)-1][i]})

    returned.append(stimulus)
    return returned


def plot_epochs(db: Session, csv_id: int, epoch_plot: EpochPlot):

    csv = csv_crud.find_by_id(db, csv_id)

    if csv is None:
        return None

    exp = experiment_crud.find_by_id(db, csv.experiment_id)
    if exp is None:
        return None

    df = pd.read_csv(csv.path)

    rawdata = load_raw(df, exp)
    epochs = get_epoch(rawdata, exp)

    figure = epochs.plot(n_epochs=epoch_plot.n_events, scalings='auto', block=True)
    figure.set_size_inches(11.5, 7.5)

    name_tmp = generate_name_tmp()
    figure.savefig(name_tmp)

    with open(name_tmp, 'rb') as f:
        base64image = base64.b64encode(f.read())
    os.remove(name_tmp)
    return base64image


def plot_average_epoch(db: Session, csv_id: int, epoch_average: EpochAverage):

    csv = csv_crud.find_by_id(db, csv_id)

    if csv is None:
        return None

    exp = experiment_crud.find_by_id(db, csv.experiment_id)
    if exp is None:
        return None

    df = pd.read_csv(csv.path)

    rawdata = load_raw(df, exp)
    epochs = get_epoch(rawdata, exp)

    average = epochs[epoch_average.label].average()
    name_tmp = generate_name_tmp()
    figure = average.plot(picks=epoch_average.channel, titles=dict(eeg='Channel ' + epoch_average.channel + ', Label: ' + epoch_average.label))
    figure.set_size_inches(11.5, 5)

    figure.savefig(name_tmp)

    with open(name_tmp, 'rb') as f:
        base64image = base64.b64encode(f.read())
    os.remove(name_tmp)
    return base64image


def plot_compare(db: Session, csv_id: int, epoch_compare: EpochCompare):

    csv = csv_crud.find_by_id(db, csv_id)

    if csv is None:
        return None

    exp = experiment_crud.find_by_id(db, csv.experiment_id)
    if exp is None:
        return None

    df = pd.read_csv(csv.path)

    rawdata = load_raw(df, exp)
    epochs = get_epoch(rawdata, exp)

    average = epochs[epoch_compare.label].average()
    name_tmp = generate_name_tmp()

    figure, ax = plt.subplots()

    mne.viz.plot_compare_evokeds(dict(target=average), axes=ax, title='Label: ' + epoch_compare.label,
                                show_sensors='upper right')
    figure.set_size_inches(11.5, 5)

    figure.savefig(name_tmp)

    with open(name_tmp, 'rb') as f:
        base64image = base64.b64encode(f.read())
    os.remove(name_tmp)
    return base64image


def plot_activity_brain(db: Session, csv_id: int, epoch_activity: EpochActivity):

    csv = csv_crud.find_by_id(db, csv_id)

    if csv is None:
        return None

    exp = experiment_crud.find_by_id(db, csv.experiment_id)
    if exp is None:
        return None

    df = pd.read_csv(csv.path)
    rawdata = load_raw(df, exp)

    epochs = get_epoch(rawdata, exp)
    average = epochs[epoch_activity.label].average()
    name_tmp = generate_name_tmp()

    figure = average.plot_topomap(times=epoch_activity.times, ch_type='eeg', extrapolate=epoch_activity.extrapolate)

    figure.set_size_inches(11.5, 5)

    figure.savefig(name_tmp)

    with open(name_tmp, 'rb') as f:
        base64image = base64.b64encode(f.read())
    os.remove(name_tmp)
    return base64image


def get_epoch(rawdata, exp):

    events = mne.find_events(rawdata, shortest_event=1)
    event_id = {}
    for label in exp.labels:
        event_id[label.description] = int(label.label)

    return mne.Epochs(rawdata, events=events, event_id=event_id, tmin=exp.epoch_start, tmax=exp.epoch_end,
                        preload=True)
