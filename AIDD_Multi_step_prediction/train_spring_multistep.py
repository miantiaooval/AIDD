import time
import torch.nn.utils as U
import torch.optim as optim
from model import *
from tools import *
import argparse
# configuration
HYP = {
    'node_size': 100,
    'hid': 128,  # hidden size
    'epoch_num': 1000,  # epoch
    'batch_size': 512,  # batch size
    'lr_net': 0.004,  # lr for net generator 0.004
    'lr_dyn': 0.001,  # lr for dyn learner
    'lr_stru': 0.0001,  # lr for structural loss 0.0001 2000 0.01  0.00001
    'hard_sample': False,  # weather to use hard mode in gumbel
    'sample_time': 1,  # sample time while training
    'temp': 1,  # temperature
    'drop_frac': 1,  # temperature drop frac
}


parser = argparse.ArgumentParser()
parser.add_argument('--nodes', type=int, default=100, help='Number of nodes, default=10')
parser.add_argument('--network', type=str, default='ER', help='type of network')
parser.add_argument('--prediction_steps', type=int, default=10, help='prediction steps')
parser.add_argument('--sys', type=str, default='spring', help='simulated system to model,spring or cmn')
parser.add_argument('--dim', type=int, default=4, help='# information dimension of each node spring:4 cmn:1 ')
parser.add_argument('--exp_id', type=int, default=1, help='experiment_id, default=1')
parser.add_argument('--device_id', type=int, default=5, help='Gpu_id, default=5')
args = parser.parse_args()

#set gpu id
torch.cuda.set_device(args.device_id)
start_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
print('start_time:', start_time)
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

# generator
generator = Gumbel_Generator_Old(sz=args.nodes, temp=HYP['temp'], temp_drop_frac=HYP['drop_frac']).to(device)
generator.init(0, 0.1)
# generator optimizer
op_net = optim.Adam(generator.parameters(), lr=HYP['lr_net'])

# dyn learner
dyn_isom = IO_B(args.dim, HYP['hid']).to(device)
# dyn learner  optimizer
op_dyn = optim.Adam(dyn_isom.parameters(), lr=HYP['lr_dyn'])

# load_data
if args.sys== 'spring':
    train_loader, val_loader, test_loader, object_matrix = load_spring_multi(batch_size=HYP['batch_size'],node_num=args.nodes,network=args.network,
                                                                             prediciton_steps=args.prediction_steps,exp_id=args.exp_id)

object_matrix = object_matrix.cpu().numpy()



def train_dyn_gen():
    loss_batch = []
    mse_batch = []

    print('current temp:', generator.temperature)

    for idx, data in enumerate(train_loader):
        print('batch idx:', idx)
        # data
        data = data.to(device)

        x = data[:, : ,0,:]
        y = data[:, :,1:, :]
        # drop temperature
        generator.drop_temp()
        outputs = torch.zeros(y.size(0), y.size(1), y.size(2)+1,y.size(3))

        outputs[:,:,0,:] = x
        temp_x = x
        # zero grad
        op_net.zero_grad()
        op_dyn.zero_grad()

        num = int(args.nodes / HYP['node_size'])
        remainder = int(args.nodes  % HYP['node_size'])
        if remainder == 0:
            num = num - 1

        #multistep prediction
        for step in range(args.prediction_steps):
            cur_temp_x = temp_x
            for j in range(args.nodes ):
                # predict and caculate the loss
                adj_col = generator.sample_adj_i(j, hard=HYP['hard_sample'], sample_time=HYP['sample_time']).to(device)
                y_hat = dyn_isom(cur_temp_x, adj_col, j, num, HYP['node_size'])
                temp_x[:,j,:] = y_hat

            outputs[:,:,step+1,:] = temp_x


        loss = torch.mean(torch.abs(outputs[:,:,1:,:] - y.cpu()))

        # backward and optimize
        loss.backward()
        # cut gradient in case nan shows up
        U.clip_grad_norm_(generator.gen_matrix, 0.000075)

        op_net.step()
        op_dyn.step()

        loss_batch.append(loss.item())
        mse_batch.append(F.mse_loss(y.cpu(), outputs[:,:,1:,:]).item())


    op_net.zero_grad()
    loss = (torch.sum(generator.sample_all())) * HYP['lr_stru']
    loss.backward()
    op_net.step()

    # each item is the mean of all batches, means this indice for one epoch
    return np.mean(loss_batch), np.mean(mse_batch),










# start training
best_val_mse = 1000000
best = 0
best_loss = 10000000

# model save path
dyn_path = './model/dyn_spring_' + args.network + '_' + str(args.nodes) + 'pre_'+str(args.prediction_steps)+'_id' + str(args.exp_id) + '.pkl'
gen_path = './model/gen_spring_' + args.network + '_' + str(args.nodes) +'pre_'+str(args.prediction_steps)+ '_id' + str(args.exp_id) + '.pkl'
adj_path = './model/adj_spring_' + args.network + '_' + str(args.nodes) + 'pre_'+str(args.prediction_steps)+'_id' + str(args.exp_id) + '.pkl'

# each training epoch
for e in range(HYP['epoch_num']):
    print('\nepoch', e)
    t_s = time.time()
    t_s1 = time.time()
    try:
        # train both dyn learner and generator together
        loss, mse = train_dyn_gen()
    except RuntimeError as sss:
        if 'out of memory' in str(sss):
            print('|WARNING: ran out of memory')
            if hasattr(torch.cuda, 'empty_cache'):
                torch.cuda.empty_cache()
        else:
            raise sss

    t_e1 = time.time()
    print('loss:' + str(loss) + ' mse:' + str(mse))
    print('time for this dyn_adj epoch:' + str(round(t_e1 - t_s1, 2)))

    if loss < best_loss:
        print('best epoch:', e)
        best_loss = loss
        best = e
        torch.save(dyn_isom, dyn_path)
        torch.save(generator, gen_path)
        out_matrix = generator.sample_all(hard=HYP['hard_sample'], ).to(device)
        torch.save(out_matrix, adj_path)
    print('best epoch:', best)
    # if e > 1:
    t_s2 = time.time()
    #Evaluate the accuracy of predict adj
    constructor_evaluator(generator, 1, np.float32(object_matrix), e)
    t_e2 = time.time()
    print('time for this adj_eva epoch:' + str(round(t_e2 - t_s2, 2)))
    t_e = time.time()
    print('time for this whole epoch:' + str(round(t_e - t_s, 2)))

end_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
print('end_time:', end_time)
