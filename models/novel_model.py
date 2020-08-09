import torch
from . import networks
from .base_model import BaseModel
from pytorch_ssim import SSIM
from watermarks import lsb, lsbm, lsbmr, rlsb, dct
from utils.util import tensor2im, bits2im, im2tensor

class NovelModel(BaseModel):
    """This class implements our novel model, which is modified from pix2pix.

    The model training requires '--dataset_mode aligned' dataset.
    By default, it uses a '--netG unet256' U-Net generator,
    a '--netD basic' discriminator (PatchGAN),
    and a '--gan_mode' vanilla GAN loss (the cross-entropy objective used in the orignal GAN paper).
    
    Our novel methods are marked by `#NUMBER`.
    """
    @staticmethod
    def modify_commandline_options(parser, is_train=True):
        """Add new dataset-specific options, and rewrite default values for existing options.

        Parameters:
            parser          -- original option parser
            is_train (bool) -- whether training phase or test phase. You can use this flag to add training-specific or test-specific options.

        Returns:
            the modified parser.

        The training objective is: GAN Loss + lambda_L1 * ||G(A)-B||_1 + lambda_SSIM * (1 - SSIM(G(A), B))
        By default, they use vanilla GAN loss, UNet with batchnorm, and aligned datasets.
        """
        # changing the default values to match the pix2pix paper (https://phillipi.github.io/pix2pix/)
        if is_train:
            parser.add_argument('--lambda_L1', type=float, default=100.0, help='weight for L1 loss')
            parser.add_argument('--lambda_SSIM', type=float, default=100.0, help='weight for SSIM loss')  #1 SSIM Loss
        return parser

    def __init__(self, opt):
        """Initialize the pix2pix class.

        Parameters:
            opt (Option class)-- stores all the experiment flags; needs to be a subclass of BaseOptions
        """
        BaseModel.__init__(self, opt)
        # specify the training losses you want to print out. The training/test scripts will call <BaseModel.get_current_losses>
        self.loss_names = ['G_GAN', 'G_L1', 'G_SSIM', 'D_real', 'D_fake']  #1 SSIM Loss
        # specify the images you want to save/display. The training/test scripts will call <BaseModel.get_current_visuals>
        self.visual_names = ['real_B_img', 'fake_B_img', 'real_watermark', 'fake_watermark']
        # specify the models you want to save to the disk. The training/test scripts will call <BaseModel.save_networks> and <BaseModel.load_networks>
        if self.isTrain:
            self.model_names = ['G', 'D']
        else:  # during test time, only load G
            self.model_names = ['G']
        # define networks (both generator and discriminator)
        if opt.expand_bits:  # expand each pixel channel to 8 bits
            opt.input_nc *= 8
            opt.output_nc *= 8
        self.netG = networks.define_G(opt.input_nc, opt.output_nc, opt.ngf, opt.netG, opt.norm,
                                      not opt.no_dropout, opt.init_type, opt.init_gain, self.gpu_ids)
        
        if self.isTrain:  # define a discriminator; conditional GANs need to take both input and output images; Therefore, #channels for D is input_nc + output_nc
            self.netD = networks.define_D(opt.input_nc + opt.output_nc, opt.ndf, opt.netD,
                                          opt.n_layers_D, opt.norm, opt.init_type, opt.init_gain, self.gpu_ids)

        if self.isTrain:
            # define loss functions
            self.criterionGAN = networks.GANLoss(opt.gan_mode).to(self.device)
            self.criterionL1 = torch.nn.L1Loss()
            self.criterionSSIM = SSIM()  #1 SSIM Loss
            # initialize optimizers; schedulers will be automatically created by function <BaseModel.setup>.
            self.optimizer_G = torch.optim.Adam(self.netG.parameters(), lr=opt.lr, betas=(opt.beta1, 0.999))
            self.optimizer_D = torch.optim.Adam(self.netD.parameters(), lr=opt.lr, betas=(opt.beta1, 0.999))
            self.optimizers.append(self.optimizer_G)
            self.optimizers.append(self.optimizer_D)

    def set_input(self, Input):
        """Unpack input data from the dataloader and perform necessary pre-processing steps.

        Parameters:
            Input (dict): include the data itself and its metadata information.

        The option 'direction' can be used to swap images in domain A and domain B.
        """
        AtoB = self.opt.direction == 'AtoB'
        
        self.real_A = Input['A' if AtoB else 'B'].to(self.device)
        if self.opt.expand_bits:
            self.real_A_img = bits2im(self.real_A)
        else:
            self.real_A_img = self.real_A.detach()
        
        self.real_B = Input['B' if AtoB else 'A'].to(self.device)
        if self.opt.expand_bits:
            self.real_B_img = bits2im(self.real_B)
        else:
            self.real_B_img = self.real_B.detach()
        
        if 'lsb' == self.opt.watermark:
            self.real_watermark = lsb.LSB().extract(tensor2im(self.real_B_img))
        elif 'lsbm' == self.opt.watermark:
            self.real_watermark = lsbm.LSBMatching(channel=2).extract(tensor2im(self.real_B_img))
        elif 'lsbmr' == self.opt.watermark:
            self.real_watermark = lsbmr.LSBMR(channel=2).extract(tensor2im(self.real_B_img))
        elif 'rlsb' == self.opt.watermark:
            self.real_watermark = rlsb.RobustLSB().extract(tensor2im(self.real_B_img), tensor2im(self.real_A_img))
        elif 'dct' == self.opt.watermark:
            self.real_watermark = dct.DCT().extract(tensor2im(self.real_B_img))
        else:
            raise NotImplementedError("Please choose implemented watermark algorithms. [lsb | lsbm | lsbmr | rlsb | dct]")
        self.image_paths = Input['A_paths' if AtoB else 'B_paths']

    def forward(self):
        """Run forward pass; called by both functions <optimize_parameters> and <test>"""
        self.fake_B = self.netG(self.real_A)  # G(A)
        if self.opt.expand_bits:
            self.fake_B_img = bits2im(self.fake_B)
        else:
            self.fake_B_img = self.fake_B.detach()
        
        if 'lsb' == self.opt.watermark:
            self.fake_watermark = lsb.LSB().extract(tensor2im(self.fake_B_img))
        elif 'lsbm' == self.opt.watermark:
            self.fake_watermark = lsbm.LSBMatching(channel=2).extract(tensor2im(self.fake_B_img))
        elif 'lsbmr' == self.opt.watermark:
            self.fake_watermark = lsbmr.LSBMR(channel=2).extract(tensor2im(self.fake_B_img))
        elif 'rlsb' == self.opt.watermark:
            self.fake_watermark = rlsb.RobustLSB().extract(tensor2im(self.fake_B_img), tensor2im(self.real_A_img))
        elif 'dct' == self.opt.watermark:
            self.fake_watermark = dct.DCT().extract(tensor2im(self.fake_B_img))
        else:
            raise NotImplementedError("Please choose implemented watermark algorithms. [lsb | lsbm | lsmr | rlsb | dct]")

    def backward_D(self):
        """Calculate GAN loss for the discriminator"""
        # Fake; stop backprop to the generator by detaching fake_B
        fake_AB = torch.cat((self.real_A, self.fake_B), 1)  # we use conditional GANs; we need to feed both input and output to the discriminator
        pred_fake = self.netD(fake_AB.detach())
        self.loss_D_fake = self.criterionGAN(pred_fake, False)
        # Real
        real_AB = torch.cat((self.real_A, self.real_B), 1)
        pred_real = self.netD(real_AB)
        self.loss_D_real = self.criterionGAN(pred_real, True)
        # combine loss and calculate gradients
        self.loss_D = (self.loss_D_fake + self.loss_D_real) * 0.5
        self.loss_D.backward()

    def backward_G(self):
        """Calculate GAN and L1 loss for the generator"""
        # First, G(A) should fake the discriminator
        fake_AB = torch.cat((self.real_A, self.fake_B), 1)
        pred_fake = self.netD(fake_AB)
        self.loss_G_GAN = self.criterionGAN(pred_fake, True)
        # Second, G(A) = B
        self.loss_G_L1 = self.criterionL1(self.fake_B, self.real_B) * self.opt.lambda_L1
        self.loss_G_SSIM = (1 - self.criterionSSIM(im2tensor(self.fake_B_img), im2tensor(self.real_B_img))) * self.opt.lambda_SSIM  #1 SSIM Loss
        # combine loss and calculate gradients
        self.loss_G = self.loss_G_GAN + self.loss_G_L1 + self.loss_G_SSIM  #1 SSIM Loss
        self.loss_G.backward()

    def optimize_parameters(self):
        """Calculate losses, gradients, and update network weights; called in every training iteration"""
        self.forward()                   # compute fake images: G(A)
        # update D
        self.set_requires_grad(self.netD, True)  # enable backprop for D
        self.optimizer_D.zero_grad()     # set D's gradients to zero
        self.backward_D()                # calculate gradients for D
        self.optimizer_D.step()          # update D's weights
        # update G
        self.set_requires_grad(self.netD, False)  # D requires no gradients when optimizing G
        self.optimizer_G.zero_grad()        # set G's gradients to zero
        self.backward_G()                   # calculate graidents for G
        self.optimizer_G.step()             # udpate G's weights