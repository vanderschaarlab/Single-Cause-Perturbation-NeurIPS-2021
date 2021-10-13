import argparse

import torch.nn as nn
import sim_config
from sim_data_gen import DataGenerator
from mlp import DirectOutcomeRegression, ModelTrainer
from utils import *
import shutil

def run(d_config, eval_only=False, n_ensemble=0, eval_delta=False):
    n_confounder = d_config.n_confounder
    n_cause = d_config.n_cause
    n_outcome = d_config.n_outcome
    sample_size = d_config.sample_size
    p_confounder_cause = d_config.p_confounder_cause
    p_cause_cause = d_config.p_cause_cause
    cause_noise = d_config.cause_noise
    outcome_noise = d_config.outcome_noise
    linear = d_config.linear
    n_flip = d_config.n_flip
    p_outcome_single = d_config.p_outcome_single
    p_outcome_double = d_config.p_outcome_double
    outcome_interaction = d_config.outcome_interaction
    sample_size_train = d_config.sample_size_train
    print('sample_size', sample_size)

    n_ensemble += 1

    batch_size = 100
    max_epoch = 100
    model_id = 'BMC'
    model_path = 'model/{}_{}_model/'.format(model_id, d_config.sim_id)
    if not eval_only:
        try:
            shutil.rmtree(model_path)
        except OSError as e:
            print("Error: %s - %s." % (e.filename, e.strerror))

    learning_rate = 0.01
    seed = 100
    hyper_param_itr = 5

    np.random.seed(seed)
    torch.manual_seed(seed)

    if d_config.real_data:
        train_ratio = 1
    else:
        train_ratio = sample_size / 1000

    if sample_size_train == 0:
        if n_ensemble == 1:
            print('generating data')
            dg = DataGenerator(n_confounder, n_cause, n_outcome, sample_size,
                               p_confounder_cause, p_cause_cause,
                               cause_noise, outcome_noise, linear=linear, confounding_level=d_config.confounding_level,
                               real_data=d_config.real_data, train_frac=0.7/train_ratio, val_frac=0.1/train_ratio,
                               p_outcome_single=p_outcome_single, p_outcome_double=p_outcome_double, outcome_interaction=outcome_interaction)
        else:
            train_sample_size = 700 * n_ensemble
            valid_sample_size = 200
            eval_sample_size = 4100
            sample_size = train_sample_size + valid_sample_size + eval_sample_size
            dg = DataGenerator(n_confounder, n_cause, n_outcome, sample_size,
                               p_confounder_cause, p_cause_cause,
                               cause_noise, outcome_noise, linear=linear, confounding_level=d_config.confounding_level,
                               real_data=d_config.real_data, train_frac=train_sample_size/sample_size, val_frac=valid_sample_size / sample_size,
                               p_outcome_single=p_outcome_single, p_outcome_double=p_outcome_double, outcome_interaction=outcome_interaction)
    else:
        valid_sample_size = 200
        eval_sample_size = 4100
        train_sample_size = sample_size_train
        sample_size = train_sample_size + valid_sample_size + eval_sample_size
        dg = DataGenerator(n_confounder, n_cause, n_outcome, sample_size,
                           p_confounder_cause, p_cause_cause,
                           cause_noise, outcome_noise, linear=linear, confounding_level=d_config.confounding_level,
                           real_data=d_config.real_data, train_frac=train_sample_size / sample_size,
                           val_frac=valid_sample_size / sample_size,
                           p_outcome_single=p_outcome_single, p_outcome_double=p_outcome_double,
                           outcome_interaction=outcome_interaction)
    if n_cause > 3:
        npc = 3
    else:
        npc = 1
    train_dataset, valid_dataset, x_test, y_test = dg.generate_dataset_bmc(npc=npc)
    print('training with ', train_dataset.tensors[0].shape[0])
    print(x_test.shape)
    print(n_confounder)
    print(n_cause)

    if not d_config.real_data:
        new_x_test, cate_test = dg.generate_counterfactual_test_bmc(n_flip)

    new_x_list = dg.generate_test_real_bmc()

    rmse = nn.MSELoss()

    err_list = list()
    param_list = get_scp_config(hyper_param_itr, n_confounder, p_confounder_cause)
    for param in param_list:
        model_id_to_save = model_id + '_itr_{}'.format(param.itr)

        model = DirectOutcomeRegression(n_confounder + npc, n_cause, n_outcome, n_hidden=param.n_outcome_rep + param.n_confounder_rep)
        optimizer = torch.optim.SGD(model.parameters(), lr=param.learning_rate)

        trainer = ModelTrainer(param.batch_size, max_epoch, rmse, model_id_to_save, model_path)

        trainer.train(model, optimizer, train_dataset, valid_dataset, print_every=10)

        load_model(model, model_path, model_id_to_save)

        with torch.no_grad():
            x_valid = valid_dataset.tensors[0]
            y_valid = valid_dataset.tensors[1]

            y_hat = model(x_valid)
            error = torch.sqrt(rmse(y_hat, y_valid))
            err_list.append(error.item())

    # select model with best hyper-parameter
    best_index = int(np.argmin(np.array(err_list)))
    best_param = param_list[best_index]
    print('Best param:', best_param)
    model_id_to_load = model_id + '_itr_{}'.format(best_param.itr)
    model = DirectOutcomeRegression(n_confounder + npc, n_cause, n_outcome,
                                    n_hidden=best_param.n_outcome_rep + best_param.n_confounder_rep)
    # load best iteration
    _, model_file = load_model(model, model_path, model_id_to_load)
    torch.save(model, model_path + 'best.pth')

    if eval_delta:
        for j in range(n_cause):
            n_flip = j + 1
            new_x_test, cate_test = dg.generate_counterfactual_test_bmc(n_flip)
            with torch.no_grad():
                y_hat0 = model(x_test)
                y_hat1 = model(new_x_test)
                cate_hat = y_hat1 - y_hat0
                error = torch.sqrt(rmse(cate_hat, cate_test))
                rmse_sd = bootstrap_RMSE((cate_hat - cate_test) ** 2)
                print('bmc', n_flip, round(error.item(), 3), round(rmse_sd, 3))
        return 0

    with torch.no_grad():
        y_list = []
        for i in range(len(new_x_list)):
            new_x = new_x_list[i]
            y_hat = model(new_x).cpu().numpy()
            y_list.append(y_hat)

    if not d_config.real_data:
        with torch.no_grad():
            y_hat0 = model(x_test)
            y_hat1 = model(new_x_test)
            cate_hat = y_hat1 - y_hat0
            error = torch.sqrt(rmse(cate_hat, cate_test))
            rmse_sd = bootstrap_RMSE((cate_hat - cate_test) ** 2)

            y_mat_true = np.concatenate(dg.outcome_list, axis=-1)
            # N, 2^K
            y_mat = np.concatenate(y_list, axis=-1)
            n_test = y_mat.shape[0]
            err_all = np.sum((y_mat_true[-n_test:, :] - y_mat) ** 2, axis=1)
            rmse_all = np.sqrt(np.mean(err_all))
            rmse_all_sd = bootstrap_RMSE(torch.tensor(err_all))

            y_mean = np.mean(y_mat_true, axis=0)[None, :]
            err_mean = np.sum((y_mat_true[-n_test:, :] - y_mean) ** 2, axis=1)
            rmse_mean = np.sqrt(np.mean(err_mean))
            print(round(rmse_mean, 3))

            print(round(error.item(), 3), round(rmse_sd, 3), round(rmse_all, 3), round(rmse_all_sd, 3))
    else:
        dg.evaluate_real(y_list)



if __name__ == '__main__':
    # config1 = sim_config.sim_dict['n_confounder_10_linear']
    # config1 = sim_config.sim_dict['real_3000']
    # run(config1, eval_only=True)

    parser = argparse.ArgumentParser('Ablation')
    parser.add_argument('--ablation', type=str, default='None')
    parser.add_argument('--config', type=str)

    args = parser.parse_args()

    config_key = args.config

    try:
        config1 = sim_config.sim_dict[config_key]
    except KeyError:
        print(config_key)
        exit(-1)

    run(config1, eval_only=False, n_ensemble=0)

