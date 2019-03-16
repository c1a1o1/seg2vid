from __future__ import print_function
import torch
from torch.autograd import Variable as Vb
from torch.utils.data import DataLoader

import os, time, sys
from tqdm import tqdm

from models.multiframe_w_mask_genmask_two_path_iterative import *
from dataset import get_test_set
from utils import utils
from opts import parse_opts

args = parse_opts()
print (args)


def make_save_dir(output_image_dir):
    val_cities = ['frankfurt', 'lindau', 'munster']
    for city in val_cities:
        pathOutputImages = os.path.join(output_image_dir, city)
        if not os.path.isdir(pathOutputImages):
            os.makedirs(pathOutputImages)


class flowgen(object):

    def __init__(self, opt):

        self.opt = opt

        print("Random Seed: ", self.opt.seed)
        torch.manual_seed(self.opt.seed)
        torch.cuda.manual_seed_all(self.opt.seed)

        dataset = opt.dataset
        self.suffix = '_' + opt.suffix

        self.refine = True
        self.useHallucination = False
        self.jobname = dataset + self.suffix
        self.modeldir = self.jobname + 'model'

        # whether to start training from an existing snapshot
        self.load = True
        self.iter_to_load = opt.iter_to_load

        ''' Cityscapes'''
        from cityscapes_dataloader_w_mask_two_path import Cityscapes

        test_Dataset = get_test_set(opt)

        self.sampledir = os.path.join('../city_scapes_test_results', self.jobname,
                                      self.suffix + '_' + str(self.iter_to_load)+'_'+str(opt.seed)+'_iterative')

        if not os.path.exists(self.sampledir):
            os.makedirs(self.sampledir)

        self.testloader = DataLoader(test_Dataset, batch_size=opt.batch_size, shuffle=False, pin_memory=True, num_workers=8)

        # Create Folder for test images.
        self.output_image_dir = self.sampledir + '_images'

        self.output_image_dir_before = self.sampledir + '_images_before'
        self.output_bw_flow_dir = self.sampledir + '_bw_flow'
        self.output_fw_flow_dir = self.sampledir + '_fw_flow'

        self.output_bw_mask_dir = self.sampledir + '_bw_mask'
        self.output_fw_mask_dir = self.sampledir + '_fw_mask'

        make_save_dir(self.output_image_dir)
        make_save_dir(self.output_image_dir_before)

        make_save_dir(self.output_bw_flow_dir)
        make_save_dir(self.output_fw_flow_dir)

        make_save_dir(self.output_fw_mask_dir)
        make_save_dir(self.output_bw_mask_dir)

    def test(self):

        opt = self.opt

        gpu_ids = range(torch.cuda.device_count())
        print ('Number of GPUs in use {}'.format(gpu_ids))

        iteration = 0

        if torch.cuda.device_count() > 1:
            vae = nn.DataParallel(VAE(hallucination=self.useHallucination, opt=opt, refine=self.refine, bg=128, fg=896), device_ids=gpu_ids).cuda()
        else:
            vae = VAE(hallucination=self.useHallucination, opt=opt).cuda()

        print(self.jobname)

        if self.load:
            model_name = '../' + self.jobname + '/{:06d}_model.pth.tar'.format(self.iter_to_load)

            print ("loading model from {}".format(model_name))

            state_dict = torch.load(model_name)
            if torch.cuda.device_count() > 1:
                vae.module.load_state_dict(state_dict['vae'])
            else:
                vae.load_state_dict(state_dict['vae'])

        z_noise = torch.ones(1, 1024).normal_()

        for data, bg_mask, fg_mask, paths in tqdm(iter(self.testloader)):
            # Set to evaluation mode (randomly sample z from the whole distribution)
            vae.eval()

            # If test on generated images
            # data = data.unsqueeze(1)
            # data = data.repeat(1, opt.num_frames, 1, 1, 1)

            frame1 = data[:, 0, :, :, :]
            noise_bg = torch.randn(frame1.size())
            z_m = Vb(z_noise.repeat(frame1.size()[0] * 8, 1))

            y_pred_before_refine, y_pred, flow, flowback, mask_fw, mask_bw, warped_mask_bg, warped_mask_fg = vae(frame1, data, bg_mask, fg_mask, noise_bg, z_m)

            '''iterative generation'''

            for i in range(5):
                noise_bg = torch.randn(frame1.size())

                y_pred_before_refine_1, y_pred_1, flow_1, flowback_1, mask_fw_1, mask_bw_1, warped_mask_bg, warped_mask_fg = vae(y_pred[:,-1,...], y_pred, warped_mask_bg, warped_mask_fg, noise_bg, z_m)

                y_pred_before_refine = torch.cat([y_pred_before_refine, y_pred_before_refine_1], 1)
                y_pred = torch.cat([y_pred, y_pred_1], 1)
                flow = torch.cat([flow, flow_1], 2)
                flowback = torch.cat([flowback, flowback_1], 2)
                mask_fw = torch.cat([mask_fw, mask_fw_1], 1)
                mask_bw = torch.cat([mask_bw, mask_bw_1], 1)

            print(y_pred_before_refine.size())

            utils.save_samples(data, y_pred_before_refine, y_pred, flow, mask_fw, mask_bw, iteration, self.sampledir, opt,
                         eval=True, useMask=True,  grid=[4, 4])

            # '''save images'''
            utils.save_images(self.output_image_dir, data, y_pred, paths, opt)
            utils.save_images(self.output_image_dir_before, data, y_pred_before_refine, paths, opt)

            iteration += 1


if __name__ == '__main__':
    a = flowgen(opt=args)
    a.test()