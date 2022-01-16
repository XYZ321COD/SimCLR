import logging
import io
import socket
from datetime import datetime
from models.resnet_simclr import ResNetSimCLR
from simclr import SimCLR
import torch.backends.cudnn as cudnn
import torch
from torch.utils.tensorboard import SummaryWriter
from tqdm import tqdm
from torchvision import datasets, transforms
import matplotlib.pyplot as plt
import PIL
from torchvision import models
import argparse
import glob
import seaborn as sn
import pandas as pd
import matplotlib.pyplot as plt
import numpy
from torch.distributions import Categorical
from sklearn.metrics import accuracy_score
model_names = sorted(name for name in models.__dict__
                     if name.islower() and not name.startswith("__")
                     and callable(models.__dict__[name]))

parser = argparse.ArgumentParser(description='PyTorch SimCLR')
parser.add_argument('-data', metavar='DIR', default='./datasets',
                    help='path to dataset')
parser.add_argument('-dataset-name', default='stl10',
                    help='dataset name', choices=['stl10', 'cifar10', 'mnist'])
parser.add_argument('-a', '--arch', metavar='ARCH', default='resnet18',
                    choices=model_names,
                    help='model architecture: ' +
                         ' | '.join(model_names) +
                         ' (default: resnet50)')
parser.add_argument('-j', '--workers', default=12, type=int, metavar='N',
                    help='number of data loading workers (default: 32)')
parser.add_argument('--epochs', default=200, type=int, metavar='N',
                    help='number of total epochs to run')
parser.add_argument('-b', '--batch-size', default=256, type=int,
                    metavar='N',
                    help='mini-batch size (default: 256), this is the total '
                         'batch size of all GPUs on the current node when '
                         'using Data Parallel or Distributed Data Parallel')
parser.add_argument('--lr', '--learning-rate', default=0.0003, type=float,
                    metavar='LR', help='initial learning rate', dest='lr')
parser.add_argument('--wd', '--weight-decay', default=1e-4, type=float,
                    metavar='W', help='weight decay (default: 1e-4)',
                    dest='weight_decay')
parser.add_argument('--seed', default=None, type=int,
                    help='seed for initializing training. ')
parser.add_argument('--disable-cuda', action='store_true',
                    help='Disable CUDA')
parser.add_argument('--fp16-precision', action='store_true',
                    help='Whether or not to use 16-bit precision GPU training.')

parser.add_argument('--out_dim', default=128, type=int,
                    help='feature dimension (default: 128)')
parser.add_argument('--log-every-n-steps', default=100, type=int,
                    help='Log every n steps')
parser.add_argument('--temperature', default=0.07, type=float,
                    help='softmax temperature (default: 0.07)')
parser.add_argument('--n-views', default=2, type=int, metavar='N',
                    help='Number of views for contrastive learning training.')
parser.add_argument('--gpu-index', default=0, type=int, help='Gpu index.')
parser.add_argument('--level_number', default=15, type=int, help='Number of nodes of the binary tree')
parser.add_argument('--save_point', default=".", type=str, help="Path to .pth ")


def arctang(p): 
    return torch.log(p/((1-p)+ 1e-8))

def eval_classification():
    torch.autograd.set_detect_anomaly(True)
    args = parser.parse_args()
    assert args.n_views == 2, "Only two view training is supported. Please use --n-views 2."
    # check if gpu training is available
    if not args.disable_cuda and torch.cuda.is_available():
        args.device = torch.device('cuda')
        cudnn.deterministic = True
        cudnn.benchmark = True
    else:
        args.device = torch.device('cpu')
        args.gpu_index = -1
   
    # Load .pth
    model_file = glob.glob(args.save_point + "/*.pth.tar")
    checkpoint = torch.load(model_file[0])
    

    model = ResNetSimCLR(base_model=args.arch, out_dim=args.out_dim, args=args)
    model.load_state_dict(checkpoint['state_dict'])
    model = model.to(args.device)
    simclr = SimCLR(model=model, optimizer=None, scheduler=None, args=args)
    clasiffier_model = torch.nn.Sequential(torch.nn.Flatten(), torch.nn.Linear(14,10)).to(args.device)
    criterion = torch.nn.CrossEntropyLoss()
    optimizer = torch.optim.Adam(clasiffier_model.parameters())
    

    if args.dataset_name == 'cifar10':
        validset = datasets.CIFAR10('./', train=False, transform=transforms.ToTensor(), download=True)
        trainset = datasets.CIFAR10('./', train=True, transform=transforms.ToTensor(), download=True)
        mnist_ex = torch.empty((3, 32, 32)) 
        classes = ('plane', 'car', 'bird', 'cat',
           'deer', 'dog', 'frog', 'horse', 'ship', 'truck')
    if args.dataset_name == 'mnist':
        validset = datasets.MNIST('./', train=False, transform=transforms.ToTensor(), download=True)
        trainset = datasets.MNIST('./', train=True, transform=transforms.ToTensor(), download=True)
        mnist_ex = torch.empty((28, 28))  
        classes = ('0', '1', '2', '3',
           '4', '5', '6', '7', '8', '9')    
    # Create valid_datasets
    valid_loader = torch.utils.data.DataLoader(validset, batch_size=128, shuffle=True, num_workers=4, pin_memory=True, drop_last=True)
    train_loader = torch.utils.data.DataLoader(trainset, batch_size=128, shuffle=True, num_workers=4, pin_memory=True, drop_last=True)

    model.eval()
    for param in model.parameters():
        param.requires_grad = False
    for epoch in range(5):  # loop over the dataset multiple times

        running_loss = 0.0
        for i, data in enumerate(train_loader, 0):
          
            # get the inputs; data is a list of [inputs, labels]
            inputs, labels = data
            inputs, labels = inputs.cuda(), labels.cuda()

            # zero the parameter gradients
            optimizer.zero_grad()

            # forward + backward + optimize
            feature = model(inputs)
            outputs_array = []
            for level in range(1, args.level_number):
                prob_features = simclr.probability_vec_with_level(feature, level)
                prob_features = prob_features + 1e-8
                outputs_array.append(prob_features)
            prob_features = torch.cat((outputs_array),1)
            prob_features = arctang(prob_features)
            inputs = prob_features
            outputs = clasiffier_model(inputs)
            loss = criterion(outputs, labels)
            loss.backward()
            optimizer.step()

            # print statistics
            running_loss += loss.item()
            if i % 200 == 199:    # print every 2000 mini-batches
                print('[%d, %5d] loss: %.3f' %
                    (epoch + 1, i + 1, running_loss / 200))
                running_loss = 0.0

    print('Finished Training') 
    correct_pred = {classname: 0 for classname in classes}
    total_pred = {classname: 0 for classname in classes}  
    correct = 0
    total = 0
    # since we're not training, we don't need to calculate the gradients for our outputs
    with torch.no_grad():
        for data in valid_loader:
            images, labels = data
            images, labels = images.cuda(), labels.cuda()
            outputs_array = []
            # calculate outputs by running images through the network
            feature = model(images)
            for level in range(1, args.level_number):
                prob_features = simclr.probability_vec_with_level(feature, level)
                prob_features = prob_features + 1e-8
                outputs_array.append(prob_features)
            prob_features = torch.cat((outputs_array),1)
            prob_features = arctang(prob_features)
            images = prob_features
            outputs = clasiffier_model(images)
            _, predicted = torch.max(outputs.data, 1)
            total += labels.size(0)
            correct += (predicted == labels).sum().item()
            for label, prediction in zip(labels, predicted):
                if label == prediction:
                    correct_pred[classes[label]] += 1
                total_pred[classes[label]] += 1

    print('Accuracy of the network on the 10000 test images: %d %%' % (
        100 * correct / total))
    for classname, correct_count in correct_pred.items():
        accuracy = 100 * float(correct_count) / total_pred[classname]
        print("Accuracy for class {:5s} is: {:.1f} %".format(classname,
                                                   accuracy))
    df_cm = pd.DataFrame([100 * float(correct_count) / total_pred[classname] for classname, correct_count in correct_pred.items()], index = [i for i in range(0,10)])
    
    plt.figure(figsize = (10,7))
    plt.title(f'Accuracy for the class')
    plt.xlabel('Accuracy')
    plt.ylabel('Label')
    sn.heatmap(df_cm, annot=True)
    plt.show()

def eval():
    args = parser.parse_args()
    assert args.n_views == 2, "Only two view training is supported. Please use --n-views 2."
    # check if gpu training is available
    if not args.disable_cuda and torch.cuda.is_available():
        args.device = torch.device('cuda')
        cudnn.deterministic = True
        cudnn.benchmark = True
    else:
        args.device = torch.device('cpu')
        args.gpu_index = -1
   
    writer = SummaryWriter(log_dir=f"./eval/{datetime.now().strftime('%b%d_%H-%M-%S') + '' + socket.gethostname()}")
    # Load .pth
    model_file = glob.glob(args.save_point + "/*.pth.tar")
    checkpoint = torch.load(model_file[0])
    
    model = ResNetSimCLR(base_model=args.arch, out_dim=args.out_dim, args=args)
    model.load_state_dict(checkpoint['state_dict'])
    model = model.to(args.device)
    simclr = SimCLR(model=model, optimizer=None, scheduler=None, args=args)

    
    # Create valid_datasets
    # validset = datasets.MNIST('./', train=False, transform=transforms.ToTensor(), download=True)
    # trainset = datasets.MNIST('./', train=True, transform=transforms.ToTensor(), download=True)
    ex = torch.empty(2**args.level_number)
    if args.dataset_name == 'cifar10':
        validset = datasets.CIFAR10('./', train=False, transform=transforms.ToTensor(), download=True)
        trainset = datasets.CIFAR10('./', train=True, transform=transforms.ToTensor(), download=True)
        mnist_ex = torch.empty((3, 32, 32)) 
    if args.dataset_name == 'mnist':
        validset = datasets.MNIST('./', train=False, transform=transforms.ToTensor(), download=True)
        trainset = datasets.MNIST('./', train=True, transform=transforms.ToTensor(), download=True)
        mnist_ex = torch.empty((28, 28))    
    valid_loader = torch.utils.data.DataLoader(validset, batch_size=1, shuffle=True, num_workers=4, pin_memory=True, drop_last=True)
    ex = torch.empty(2**args.level_number)
    histograms_for_each_label_per_level = {level : numpy.array([numpy.zeros_like(torch.empty(2**level)) for i in range(0, 10)])  for level in range(1, args.level_number+1)}
    image_for_each_cluster_per_level = {level : numpy.array([numpy.zeros_like(mnist_ex) for i in range(0,2**args.level_number)])  for level in range(1, args.level_number+1)}
    images_with_highest_entropy_per_level = {level :{ i: [0,numpy.zeros_like(mnist_ex)] for i in range(10)}  for level in range(1, args.level_number+1)}
    # histogram_for_each_label = numpy.array([numpy.zeros_like(ex) for i in range(0, 10)])   
    # image_for_each_cluster = numpy.array([numpy.zeros_like(mnist_ex) for i in range(0,2**args.level_number)])
    # images_with_highest_entropy = {i: [0,numpy.zeros_like(mnist_ex)] for i in range(10)}
    model.eval()
    
    for i, (image, label) in enumerate(tqdm(valid_loader)):
        image, label = image.cuda(), label.cuda()
        feature = model(image)
        for level in range(1, args.level_number+1):
            prob_features = simclr.probability_vec_with_level(feature, level)
            entropy = Categorical(probs = prob_features).entropy()
            for key in images_with_highest_entropy_per_level[level].keys():
                if entropy > images_with_highest_entropy_per_level[level][key][0]:
                    images_with_highest_entropy_per_level[level][key][1] = image.squeeze(0).squeeze(0).cpu().detach().numpy()
                    images_with_highest_entropy_per_level[level][key][0] = entropy
                    break
            histograms_for_each_label_per_level[level][label.item()][torch.argmax(prob_features).item()] += 1
            image_for_each_cluster_per_level[level][torch.argmax(prob_features).item()] += image.squeeze(0).squeeze(0).cpu().detach().numpy()
            image_for_each_cluster_per_level[level][torch.argmax(prob_features).item()] = image_for_each_cluster_per_level[level][torch.argmax(prob_features).item()] / 2
    for level in range(1, args.level_number+1):
        df_cm = pd.DataFrame(histograms_for_each_label_per_level[level], index = [i for i in range(0,10)],
                    columns = [i for i in range(0,2**level)])
        plt.figure(figsize = (10,7))
        plt.title(f'Confusion matrix at level {level}')
        plt.xlabel('Cluster')
        plt.ylabel('Label')
        sn.heatmap(df_cm, annot=True)
        buf = io.BytesIO()
        plt.savefig(buf, format='png')
        buf.seek(0)
        image = PIL.Image.open(buf)
        image = transforms.ToTensor()(image)          
        writer.add_image(f'Confusion matrix at level {level}', image)
        for u in range(0,2**level):
            plt.figure(figsize = (10,7))
            plt.title(f'Mean of the images at level {level} that ended up in cluster number {u}')
            plt.imshow(((image_for_each_cluster_per_level[level][u])).reshape(32,32,3))
            # plt.imshow(image_for_each_cluster_per_level[level][u], cmap='gray')
            buf = io.BytesIO()
            plt.savefig(buf, format='png')
            buf.seek(0)
            image = PIL.Image.open(buf)
            image = transforms.ToTensor()(image)          
            writer.add_image(f'Mean of the images at level {level} that ended up in cluster number {u}', image)
        for u in range(0,10):
            plt.figure(figsize = (10,7))
            plt.title(f'Example visualization of images with highest entropy - value of entropy at level {level} img number {u}:{images_with_highest_entropy_per_level[level][u][0].item()}')
            plt.imshow(((images_with_highest_entropy_per_level[level][u][1])).reshape(32,32,3))
            # plt.imshow(images_with_highest_entropy_per_level[level][u][1], cmap='gray')
            buf = io.BytesIO()
            plt.savefig(buf, format='png')
            buf.seek(0)
            image = PIL.Image.open(buf)
            image = transforms.ToTensor()(image)          
            writer.add_image(f'Example visualization of images with highest entropy - value of entropy at level {level} img number {u}:{images_with_highest_entropy_per_level[level][u][0].item()}', image)

if __name__ == "__main__":
    # eval_classification()
    eval()