''' FSL IO '''

import os
from os.path import join as pjoin
from subprocess import Popen,PIPE

import numpy as np
import numpy.linalg as npl
from numpy import newaxis

from scipy.ndimage import map_coordinates as mc

import nibabel as nib
from nibabel.tmpdirs import InTemporaryDirectory

_VAL_FMT = '   %e'

class FSLError(Exception):
    """ Class signals error in FSL processing """


def have_flirt():
    """ Return True if we can call flirt without error

    Relies on the fact that flirt produces text on stdout when called with no
    arguments
    """
    p = Popen('flirt', stdout=PIPE, stderr=PIPE, shell=True)
    stdout, stderr = p.communicate()
    return stdout != ''


def write_bvals_bvecs(bvals, bvecs, outpath=None, prefix=''):
    ''' Write FSL FDT bvals and bvecs files

    Parameters
    -------------
    bvals : (N,) sequence
       Vector with diffusion gradient strength (one per diffusion
       acquisition, N=no of acquisitions)
    bvecs : (N, 3) array-like
       diffusion gradient directions
    outpath : None or str
       path to write FDT bvals, bvecs text files
       None results in current working directory.
    prefix : str
       prefix for bvals, bvecs files in directory.  Defaults to ''
    '''
    if outpath is None:
        outpath = os.getcwd()
    bvals = tuple(bvals)
    bvecs = np.asarray(bvecs)
    bvecs[np.isnan(bvecs)] = 0
    N = len(bvals)
    fname = pjoin(outpath, prefix + 'bvals')
    fmt = _VAL_FMT * N + '\n'
    open(fname, 'wt').write(fmt % bvals)
    fname = pjoin(outpath, prefix + 'bvecs')
    bvf = open(fname, 'wt')
    for dim_vals in bvecs.T:
        bvf.write(fmt % tuple(dim_vals))


def flirt2aff(mat, in_img, ref_img):
    """ Transform from `in_img` voxels to `ref_img` voxels given `mat`

    Parameters
    ----------
    mat : (4,4) array
        contents (as array) of output ``-omat`` transformation file from flirt
    in_img : img
        image passed (as filename) to flirt as ``-in`` image
    ref_img : img
        image passed (as filename) to flirt as ``-ref`` image

    Returns
    -------
    aff : (4,4) array
        Transform from voxel coordinates in ``in_img`` to voxel coordinates in
        ``ref_img``

    Notes
    -----
    Thanks to Mark Jenkinson and Jesper Andersson for the correct statements
    here, apologies for any errors we've added.

    ``flirt`` registers an ``in`` image to a ``ref`` image.  It can produce
    (with the ``-omat`` option) - a 4 x 4 affine matrix giving the mapping from
    *inspace* to *refspace*.

    The rest of this note is to specify what *inspace* and *refspace* are.

    In what follows, a *voxtrans* for an image is the 4 by 4 affine
    ``np.diag([vox_i, vox_j, vox_k, 1])`` where ``vox_i`` etc are the voxel
    sizes for the first second and third voxel dimension.  ``vox_i`` etc are
    always positive.

    If the input image has an affine with a negative determinant, then the
    mapping from voxel coordinates in the input image to *inspace* is simply
    *voxtrans* for the input image.  If the reference image has a negative
    determinant, the mapping from voxel space in the reference image to
    *refspace* is simply *voxtrans* for the reference image.

    A negative determinant for the image affine is the common case, of an image
    with a x voxel flip.  Analyze images don't store affines and flirt assumes a
    negative determinant in these cases.

    For positive determinant affines, flirt starts *inspace* and / or *refspace*
    with an x voxel flip.  The mapping implied for an x voxel flip for image
    with shape (N_i, N_j, N_k) is:

        [[-1, 0, 0, N_i - 1],
         [ 0, 1, 0,       0],
         [ 0, 0, 1,       0],
         [ 0, 0, 0,       1]]

    If the input image has an affine with a positive determinant, then mapping
    from input image voxel coordinates to *inspace* is ``np.dot(input_voxtrans,
    input_x_flip)`` - where ``input_x_flip`` is the matrix above with ``N_i``
    given by the input image first axis length.  Similarly the mapping from
    reference voxel coordinates to *refspace*, if the reference image has a
    positive determinant, is ``np.dot(ref_voxtrans, ref_x_flip)`` - where
    ``ref_x_flip`` is the matrix above with ``N_i`` given by the reference image
    first axis length.
    """
    in_hdr = in_img.get_header()
    ref_hdr = ref_img.get_header()
    # get_zooms gets the positive voxel sizes as returned in the header
    inspace = np.diag(in_hdr.get_zooms() + (1,))
    refspace = np.diag(ref_hdr.get_zooms() + (1,))
    if npl.det(in_img.get_affine())>=0:
        inspace = np.dot(inspace, _x_flipper(in_hdr.get_data_shape()[0]))
    if npl.det(ref_img.get_affine())>=0:
        refspace = np.dot(refspace, _x_flipper(ref_hdr.get_data_shape()[0]))
    # Return voxel to voxel mapping
    return np.dot(npl.inv(refspace), np.dot(mat, inspace))


def _x_flipper(N_i):
    flipr = np.diag([-1, 1, 1, 1])
    flipr[0,3] = N_i - 1
    return flipr


def flirt2aff_files(matfile, in_fname, ref_fname):
    """ Map from `in_fname` image voxels to `ref_fname` voxels given `matfile`

    See :func:`flirt2aff` docstring for details.

    Parameters
    ------------
    matfile : str
        filename of output ``-omat`` transformation file from flirt
    in_fname : str
        filename for image passed to flirt as ``-in`` image
    ref_fname : str
        filename for image passed to flirt as ``-ref`` image

    Returns
    -------
    aff : (4,4) array
        Transform from voxel coordinates in image for ``in_fname`` to voxel
        coordinates in image for ``ref_fname``
    """
    mat = np.loadtxt(matfile)
    in_img = nib.load(in_fname)
    ref_img = nib.load(ref_fname)
    return flirt2aff(mat, in_img, ref_img)


def warp_displacements(ffa,flaff,fdis,fref,ffaw,order=1):
    ''' Warp an image using fsl displacements 

    Parameters
    ------------
    ffa : filename of nifti to be warped
    flaff : filename of .mat  (flirt)
    fdis :  filename of displacements (fnirtfileutils)
    fref : filename of reference volume e.g. (FMRIB58_FA_1mm.nii.gz)
    ffaw : filename for the output warped image
    '''
    refaff=nib.load(fref).get_affine()    
    disdata=nib.load(fdis).get_data()
    imgfa=nib.load(ffa)
    fadata=imgfa.get_data()
    fazooms=imgfa.get_header().get_zooms()    
    #from fa index to ref index
    res=flirt2aff_files(flaff,ffa,fref)
    #from ref index to fa index
    ires=np.linalg.inv(res)    
    #create the 4d volume which has the indices for the reference image  
    reftmp=np.zeros(disdata.shape)
    '''    
    #create the grid indices for the reference
    #refinds = np.ndindex(disdata.shape[:3])  
    for ijk_t in refinds:
        i,j,k = ijk_t   
        reftmp[i,j,k,0]=i
        reftmp[i,j,k,1]=j
        reftmp[i,j,k,2]=k
    '''
    #same as commented above but much faster
    reftmp[...,0] = np.arange(disdata.shape[0])[:,newaxis,newaxis]
    reftmp[...,1] = np.arange(disdata.shape[1])[newaxis,:,newaxis]
    reftmp[...,2] = np.arange(disdata.shape[2])[newaxis,newaxis,:]
        
    #affine transform from reference index to the fa index
    A = np.dot(reftmp,ires[:3,:3].T)+ires[:3,3]
    #add the displacements but first devide them by the voxel sizes
    A2=A+disdata/fazooms
    #hold the displacements' shape reshaping
    di,dj,dk,dl=disdata.shape
    #do the interpolation using map coordinates
    #the list of points where the interpolation is done given by the reshaped in 2D A2 (list of 3d points in fa index)
    W=mc(fadata,A2.reshape(di*dj*dk,dl).T,order=order).reshape(di,dj,dk)    
    #save the warped image
    Wimg=nib.Nifti1Image(W,refaff)
    nib.save(Wimg,ffaw)
    
def pipe(cmd):
    """ A tine pipeline system to run external tools.
            
    For more advanced pipelining use nipype http://www.nipy.org/nipype    
    """
    p = Popen(cmd, shell=True,stdout=PIPE,stderr=PIPE)
    sto=p.stdout.readlines()
    ste=p.stderr.readlines()
    print(sto)
    print(ste)


def dcm2nii(dname,outdir,filt='*.dcm',options='-d n -g n -i n -o'):
    cmd='dcm2nii '+options +' ' + outdir +' ' + dname + '/' + filt
    print(cmd)
    pipe(cmd)


def eddy_correct(in_nii,out_nii,ref=0):
    cmd='eddy_correct '+in_nii+' '+ out_nii + ' '+str(ref)
    print(cmd)
    pipe(cmd)


def bet(in_nii,out_nii,options=' -F -f .2 -g 0'):
    cmd='bet '+in_nii+' '+ out_nii + options
    print(cmd)
    pipe(cmd)


def flirt(in_img, ref_img, dof=6, flags=''):
    """ Run flirt on nibabel images, returning affine

    Parameters
    ----------
    in_img : `SpatialImage'
        image to register
    ref_img : `SpatialImage`
        image to register to
    dof : int, optional
        degrees of freedom for registration (default 6)
    flags : str, optional
        other flags to pass to flirt command string

    Returns
    -------
    in_vox2out_vox : (4,4) ndarray
        affine such that, if [i, j, k] is a coordinate in voxels in the
        `in_img`, and [p, q, r] are the equivalent voxel coordinates in the
        reference image, then [p, q, r] = np.dot(in_vox2out_vox[:3,:3]), [i, j,
        k] + in_vox2out_vox[:3,3])
    """
    omat = 'reg.mat'
    with InTemporaryDirectory():
        nib.save(in_img, 'in.nii')
        nib.save(ref_img, 'ref.nii')
        cmd = 'flirt %s -dof %d -in in.nii -ref ref.nii -omat %s' % (
            flags, dof, omat)
        proc = Popen(cmd, shell=True,stdout=PIPE,stderr=PIPE)
        stdout, stderr = proc.communicate()
        if not os.path.isfile(omat):
            raise FSLError('Command "%s" failed somehow - stdout: %s\n'
                           'and stderr: %s\n' % (cmd, stdout, stderr))
        res = np.loadtxt(omat)
    return flirt2aff(res, in_img, ref_img)


def apply_warp(in_nii,affine_mat,nonlin_nii,out_nii):
    cmd='applywarp --ref=${FSLDIR}/data/standard/FMRIB58_FA_1mm --in='+in_nii+' --warp='+nonlin_nii+' --out='+out_nii
    print(cmd)
    pipe(cmd)

def create_displacements(in_nii,affine_mat,nonlin_nii,invw_nii,disp_nii,dispa_nii):
    commands=[]    
    commands.append('flirt -ref ${FSLDIR}/data/standard/FMRIB58_FA_1mm -in '+in_nii+' -omat ' + affine_mat)
    commands.append('fnirt --in='+in_nii+' --aff='+affine_mat+' --cout='+nonlin_nii+' --config=FA_2_FMRIB58_1mm')
    commands.append('invwarp --ref='+in_nii+' --warp='+nonlin_nii+' --out='+invw_nii)
    commands.append('fnirtfileutils --in='+nonlin_nii+' --ref=${FSLDIR}/data/standard/FMRIB58_FA_1mm --out='+disp_nii)
    commands.append('fnirtfileutils --in='+nonlin_nii+' --ref=${FSLDIR}/data/standard/FMRIB58_FA_1mm --out='+dispa_nii + ' --withaff')
    for c in commands:
        print(c)
        pipe(c)
