# -*- coding: utf-8 -*-

#--------------------------------------------------------------------------
# Software:     InVesalius - Software de Reconstrucao 3D de Imagens Medicas
# Copyright:    (C) 2001  Centro de Pesquisas Renato Archer
# Homepage:     http://www.softwarepublico.gov.br
# Contact:      invesalius@cti.gov.br
# License:      GNU - GPL 2 (LICENSE.txt/LICENCA.txt)
#--------------------------------------------------------------------------
#    Este programa e software livre; voce pode redistribui-lo e/ou
#    modifica-lo sob os termos da Licenca Publica Geral GNU, conforme
#    publicada pela Free Software Foundation; de acordo com a versao 2
#    da Licenca.
#
#    Este programa eh distribuido na expectativa de ser util, mas SEM
#    QUALQUER GARANTIA; sem mesmo a garantia implicita de
#    COMERCIALIZACAO ou de ADEQUACAO A QUALQUER PROPOSITO EM
#    PARTICULAR. Consulte a Licenca Publica Geral GNU para obter mais
#    detalhes.
#--------------------------------------------------------------------------

# Author: Victor Hugo Souza (victorhos-at-hotmail.com)
# Contributions: Dogu Baran Aydogan
# Initial date: 8 May 2020

import threading
import time

import numpy as np
import queue
from invesalius.pubsub import pub as Publisher
from scipy.stats import norm
import vtk

import invesalius.constants as const
import invesalius.data.imagedata_utils as img_utils

import invesalius.project as prj

# Nice print for arrays
# np.set_printoptions(precision=2)
# np.set_printoptions(suppress=True)


def compute_directions(trk_n, alpha=255):
    """Compute direction of a single tract in each point and return as an RGBA color

    :param trk_n: nx3 array of doubles (x, y, z) point coordinates composing the tract
    :type trk_n: numpy.ndarray
    :param alpha: opacity value in the interval [0, 255]. The 0 is no opacity (total transparency).
    :type alpha: int
    :return: nx3 array of int (x, y, z) RGB colors in the range 0 - 255
    :rtype: numpy.ndarray
    """

    # trk_d = np.diff(trk_n, axis=0, append=2*trk_n[np.newaxis, -1, :])
    trk_d = np.diff(trk_n, axis=0, append=trk_n[np.newaxis, -2, :])
    trk_d[-1, :] *= -1
    # check that linalg norm makes second norm
    # https://stackoverflow.com/questions/21030391/how-to-normalize-an-array-in-numpy
    direction = 255 * np.absolute((trk_d / np.linalg.norm(trk_d, axis=1)[:, None]))
    direction = np.hstack([direction, alpha * np.ones([direction.shape[0], 1])])
    return direction.astype(int)


def compute_tubes(trk, direction):
    """Compute and assign colors to a vtkTube for visualization of a single tract

    :param trk: nx3 array of doubles (x, y, z) point coordinates composing the tract
    :type trk: numpy.ndarray
    :param direction: nx3 array of int (x, y, z) RGB colors in the range 0 - 255
    :type direction: numpy.ndarray
    :return: a vtkTubeFilter instance
    :rtype: vtkTubeFilter
    """

    numb_points = trk.shape[0]
    points = vtk.vtkPoints()
    lines = vtk.vtkCellArray()

    colors = vtk.vtkUnsignedCharArray()
    colors.SetNumberOfComponents(4)

    k = 0
    lines.InsertNextCell(numb_points)
    for j in range(numb_points):
        points.InsertNextPoint(trk[j, :])
        colors.InsertNextTuple(direction[j, :])
        lines.InsertCellPoint(k)
        k += 1

    trk_data = vtk.vtkPolyData()
    trk_data.SetPoints(points)
    trk_data.SetLines(lines)
    trk_data.GetPointData().SetScalars(colors)

    # make it a tube
    trk_tube = vtk.vtkTubeFilter()
    trk_tube.SetRadius(0.5)
    trk_tube.SetNumberOfSides(4)
    trk_tube.SetInputData(trk_data)
    trk_tube.Update()

    return trk_tube


def combine_tracts_root(out_list, root, n_block):
    """Adds a set of tracts to given position in a given vtkMultiBlockDataSet

    :param out_list: List of vtkTubeFilters representing the tracts
    :type out_list: list
    :param root: A collection of tracts as a vtkMultiBlockDataSet
    :type root: vtkMultiBlockDataSet
    :param n_block: The location in the given vtkMultiBlockDataSet to insert the new tracts
    :type n_block: int
    :return: The updated collection of tracts as a vtkMultiBlockDataSet
    :rtype: vtkMultiBlockDataSet
    """

    # create tracts only when at least one was computed
    # print("Len outlist in root: ", len(out_list))
    if not out_list.count(None) == len(out_list):
        for n, tube in enumerate(out_list):
            root.SetBlock(n_block + n, tube.GetOutput())

    return root


def combine_tracts_branch(out_list):
    """Combines a set of tracts in vtkMultiBlockDataSet

    :param out_list: List of vtkTubeFilters representing the tracts
    :type out_list: list
    :return: A collection of tracts as a vtkMultiBlockDataSet
    :rtype: vtkMultiBlockDataSet
    """

    branch = vtk.vtkMultiBlockDataSet()
    # create tracts only when at least one was computed
    # print("Len outlist in root: ", len(out_list))
    if not out_list.count(None) == len(out_list):
        for n, tube in enumerate(out_list):
            branch.SetBlock(n, tube.GetOutput())

    return branch


def tracts_computation(trk_list, root, n_tracts):
    """Convert the list of all computed tracts given by Trekker run and returns a vtkMultiBlockDataSet

    :param trk_list: List of lists containing the computed tracts and corresponding coordinates
    :type trk_list: list
    :param root: A collection of tracts as a vtkMultiBlockDataSet
    :type root: vtkMultiBlockDataSet
    :param n_tracts:
    :type n_tracts: int
    :return: The updated collection of tracts as a vtkMultiBlockDataSet
    :rtype: vtkMultiBlockDataSet
    """

    # Transform tracts to array
    trk_arr = [np.asarray(trk_n).T if trk_n else None for trk_n in trk_list]

    # Compute the directions
    trk_dir = [compute_directions(trk_n) for trk_n in trk_arr]

    # Compute the vtk tubes
    out_list = [compute_tubes(trk_arr_n, trk_dir_n) for trk_arr_n, trk_dir_n in zip(trk_arr, trk_dir)]

    root = combine_tracts_root(out_list, root, n_tracts)

    return root


def compute_tracts(trekker, position, affine, affine_vtk, n_tracts):
    """ Compute tractograms using the Trekker library.

    :param trekker: Trekker library instance
    :type trekker: Trekker.T
    :param position: 3 double coordinates (x, y, z) in list or array
    :type position: list
    :param affine: 4 x 4 numpy double array
    :type affine: numpy.ndarray
    :param affine_vtk: vtkMatrix4x4 isntance with affine transformation matrix
    :type affine_vtk: vtkMatrix4x4
    :param n_tracts: number of tracts to compute
    :type n_tracts: int
    """

    # during neuronavigation, root needs to be initialized outside the while loop so the new tracts
    # can be appended to the root block set
    root = vtk.vtkMultiBlockDataSet()
    # Juuso's
    # seed = np.array([[-8.49, -8.39, 2.5]])
    # Baran M1
    # seed = np.array([[27.53, -77.37, 46.42]])
    seed_trk = img_utils.convert_world_to_voxel(position, affine)
    # print("seed example: {}".format(seed_trk))
    trekker.seed_coordinates(np.repeat(seed_trk, n_tracts, axis=0))
    # print("trk list len: ", len(trekker.run()))
    trk_list = trekker.run()
    if trk_list:
        root = tracts_computation(trk_list, root, 0)
        Publisher.sendMessage('Remove tracts')
        Publisher.sendMessage('Update tracts', root=root, affine_vtk=affine_vtk, coord_offset=position)
    else:
        Publisher.sendMessage('Remove tracts')


def tracts_computation_branch(trk_list, alpha=255):
    """Convert the list of all computed tracts given by Trekker run and returns a vtkMultiBlockDataSet

    :param trk_list: List of lists containing the computed tracts and corresponding coordinates
    :type trk_list: list
    :param alpha: opacity value in the interval [0, 255]. The 0 is no opacity (total transparency).
    :type alpha: int
    :return: The collection of tracts as a vtkMultiBlockDataSet
    :rtype: vtkMultiBlockDataSet
    """
    # Transform tracts to array
    trk_arr = [np.asarray(trk_n).T if trk_n else None for trk_n in trk_list]
    # Compute the directions
    trk_dir = [compute_directions(trk_n, alpha) for trk_n in trk_arr]
    # Compute the vtk tubes
    tube_list = [compute_tubes(trk_arr_n, trk_dir_n) for trk_arr_n, trk_dir_n in zip(trk_arr, trk_dir)]
    branch = combine_tracts_branch(tube_list)

    return branch


class ComputeTractsThread(threading.Thread):

    def __init__(self, inp, queues, event, sle):
        """Class (threading) to compute real time tractography data for visualization.

        Tracts are computed using the Trekker library by Baran Aydogan (https://dmritrekker.github.io/)
        For VTK visualization, each tract (fiber) is a constructed as a tube and many tubes combined in one
        vtkMultiBlockDataSet named as a branch. Several branches are combined in another vtkMultiBlockDataSet named as
        bundle, to obtain fast computation and visualization. The bundle dataset is mapped to a single vtkActor.
        Mapper and Actor are computer in the data/viewer_volume.py module for easier handling in the invesalius 3D scene.

        Sleep function in run method is used to avoid blocking GUI and more fluent, real-time navigation

        :param inp: List of inputs: trekker instance, affine numpy array, seed_offset, seed_radius, n_threads
        :type inp: list
        :param queues: Queue list with coord_tracts_queue (Queue instance that manage co-registered coordinates) and
         tracts_queue (Queue instance that manage the tracts to be visualized)
        :type queues: list[queue.Queue, queue.Queue]
        :param event: Threading event to coordinate when tasks as done and allow UI release
        :type event: threading.Event
        :param sle: Sleep pause in seconds
        :type sle: float
        """

        threading.Thread.__init__(self, name='ComputeTractsThread')
        self.inp = inp
        # self.coord_queue = coord_queue
        self.coord_tracts_queue = queues[0]
        self.tracts_queue = queues[1]
        # self.visualization_queue = visualization_queue
        self.event = event
        self.sle = sle

    def run(self):

        trekker, affine, offset, n_tracts_total, seed_radius, n_threads, act_data, affine_vtk, img_shift = self.inp
        # n_threads = n_tracts_total
        p_old = np.array([[0., 0., 0.]])
        n_tracts = 0

        # Compute the tracts
        # print('ComputeTractsThread: event {}'.format(self.event.is_set()))
        while not self.event.is_set():
            try:
                # print("Computing tracts")
                # get from the queue the coordinates, coregistration transformation matrix, and flipped matrix
                # print("Here")
                m_img_flip = self.coord_tracts_queue.get_nowait()
                # coord, m_img, m_img_flip = self.coord_queue.get_nowait()
                # print('ComputeTractsThread: get {}'.format(count))

                # TODO: Remove this is not needed
                # 20200402: in this new refactored version the m_img comes different than the position
                # the new version m_img is already flixped in y, which means that Y is negative
                # if only the Y is negative maybe no need for the flip_x funtcion at all in the navigation
                # but check all coord_queue before why now the m_img comes different than position
                # 20200403: indeed flip_x is just a -1 multiplication to the Y coordinate, remove function flip_x
                # m_img_flip = m_img.copy()
                # m_img_flip[1, -1] = -m_img_flip[1, -1]

                # translate the coordinate along the normal vector of the object/coil
                coord_offset = m_img_flip[:3, -1] - offset * m_img_flip[:3, 2]
                # coord_offset = np.array([[27.53, -77.37, 46.42]])
                dist = abs(np.linalg.norm(p_old - np.asarray(coord_offset)))
                p_old = coord_offset.copy()

                # print("p_new_shape", coord_offset.shape)
                # print("m_img_flip_shape", m_img_flip.shape)
                seed_trk = img_utils.convert_world_to_voxel(coord_offset, affine)
                # Juuso's
                # seed_trk = np.array([[-8.49, -8.39, 2.5]])
                # Baran M1
                # seed_trk = np.array([[27.53, -77.37, 46.42]])
                # print("Seed: {}".format(seed))

                # set the seeds for trekker, one seed is repeated n_threads times
                # trekker has internal multiprocessing approach done in C. Here the number of available threads is give,
                # but in case a large number of tracts is requested, it will compute all in parallel automatically
                # for a more fluent navigation, better to compute the maximum number the computer handles
                trekker.seed_coordinates(np.repeat(seed_trk, n_threads, axis=0))

                # run the trekker, this is the slowest line of code, be careful to just use once!
                trk_list = trekker.run()

                if trk_list:
                    # print("dist: {}".format(dist))
                    if dist >= seed_radius:
                        # when moving the coil further than the seed_radius restart the bundle computation
                        bundle = vtk.vtkMultiBlockDataSet()
                        n_branches = 0
                        branch = tracts_computation_branch(trk_list)
                        bundle.SetBlock(n_branches, branch)
                        n_branches += 1
                        n_tracts = branch.GetNumberOfBlocks()

                    # TODO: maybe keep computing even if reaches the maximum
                    elif dist < seed_radius and n_tracts < n_tracts_total:
                        # compute tracts blocks and add to bungle until reaches the maximum number of tracts
                        branch = tracts_computation_branch(trk_list)
                        if bundle:
                            bundle.SetBlock(n_branches, branch)
                            n_tracts += branch.GetNumberOfBlocks()
                            n_branches += 1

                else:
                    bundle = None

                # rethink if this should be inside the if condition, it may lock the thread if no tracts are found
                # use no wait to ensure maximum speed and avoid visualizing old tracts in the queue, this might
                # be more evident in slow computer or for heavier tract computations, it is better slow update
                # than visualizing old data
                # self.visualization_queue.put_nowait([coord, m_img, bundle])
                self.tracts_queue.put_nowait((bundle, affine_vtk, coord_offset))
                # print('ComputeTractsThread: put {}'.format(count))

                self.coord_tracts_queue.task_done()
                # self.coord_queue.task_done()
                # print('ComputeTractsThread: done {}'.format(count))

                # sleep required to prevent user interface from being unresponsive
                time.sleep(self.sle)
            # if no coordinates pass
            except queue.Empty:
                # print("Empty queue in tractography")
                pass
            # if queue is full mark as done (may not be needed in this new "nowait" method)
            except queue.Full:
                # self.coord_queue.task_done()
                self.coord_tracts_queue.task_done()


class ComputeTractsACTThread(threading.Thread):

    def __init__(self, inp, queues, event, sle):
        """Class (threading) to compute real time tractography data for visualization.

        Tracts are computed using the Trekker library by Baran Aydogan (https://dmritrekker.github.io/)
        For VTK visualization, each tract (fiber) is a constructed as a tube and many tubes combined in one
        vtkMultiBlockDataSet named as a branch. Several branches are combined in another vtkMultiBlockDataSet named as
        bundle, to obtain fast computation and visualization. The bundle dataset is mapped to a single vtkActor.
        Mapper and Actor are computer in the data/viewer_volume.py module for easier handling in the invesalius 3D scene.

        Sleep function in run method is used to avoid blocking GUI and more fluent, real-time navigation

        :param inp: List of inputs: trekker instance, affine numpy array, seed_offset, seed_radius, n_threads
        :type inp: list
        :param queues: Queue list with coord_tracts_queue (Queue instance that manage co-registered coordinates) and
         tracts_queue (Queue instance that manage the tracts to be visualized)
        :type queues: list[queue.Queue, queue.Queue]
        :param event: Threading event to coordinate when tasks as done and allow UI release
        :type event: threading.Event
        :param sle: Sleep pause in seconds
        :type sle: float
        """

        threading.Thread.__init__(self, name='ComputeTractsThreadACT')
        self.inp = inp
        # self.coord_queue = coord_queue
        self.coord_tracts_queue = queues[0]
        self.tracts_queue = queues[1]
        # on first pilots (january 12, 2021) used (-4, 4)
        self.coord_list_w = img_utils.create_grid((-2, 2), (0, 20), inp[2]-5, 1)
        # self.coord_list_sph = img_utils.create_spherical_grid(10, 1)
        # self.coord_list_sph = img_utils.create_spherical_grid(10, 1)
        # x_norm = np.linspace(norm.ppf(0.01), norm.ppf(0.99), 2*self.coord_list_sph.shape[0])
        # self.pdf = np.flipud(norm.pdf(x_norm[:self.coord_list_sph.shape[0]], loc=0, scale=2.))
        # self.sph_idx = np.linspace(0, self.coord_list_sph.shape[0], num=self.coord_list_sph.shape[0], dtype=int)
        # self.visualization_queue = visualization_queue
        self.event = event
        self.sle = sle

        # prj_data = prj.Project()
        # matrix_shape = tuple(prj_data.matrix_shape)
        # self.img_shift = matrix_shape[1]

    def run(self):

        # trekker, affine, offset, n_tracts_total, seed_radius, n_threads = self.inp
        trekker, affine, offset, n_tracts_total, seed_radius, n_threads, act_data, affine_vtk, img_shift = self.inp

        # n_threads = n_tracts_total
        p_old = np.array([[0., 0., 0.]])
        p_old_pre = np.array([[0., 0., 0.]])
        coord_offset = None
        n_tracts = 0
        n_branches = 0
        bundle = None
        sph_sampling = True
        dist_radius = 1.5

        xyz = img_utils.random_sample_sphere(radius=seed_radius, size=100)
        coord_list_sph = np.hstack([xyz, np.ones([xyz.shape[0], 1])]).T
        m_seed = np.identity(4)
        # Compute the tracts
        # print('ComputeTractsThread: event {}'.format(self.event.is_set()))
        while not self.event.is_set():
            try:
                # print("Computing tracts")
                # get from the queue the coordinates, coregistration transformation matrix, and flipped matrix
                m_img_flip = self.coord_tracts_queue.get_nowait()
                # coord, m_img, m_img_flip = self.coord_queue.get_nowait()
                # print('ComputeTractsThread: get {}'.format(count))

                # TODO: Remove this is not needed
                # 20200402: in this new refactored version the m_img comes different than the position
                # the new version m_img is already flixped in y, which means that Y is negative
                # if only the Y is negative maybe no need for the flip_x funtcion at all in the navigation
                # but check all coord_queue before why now the m_img comes different than position
                # 20200403: indeed flip_x is just a -1 multiplication to the Y coordinate, remove function flip_x
                # m_img_flip = m_img.copy()
                # m_img_flip[1, -1] = -m_img_flip[1, -1]

                # DEBUG: Uncomment the m_img_flip below so that distance is fixed and tracts keep computing
                # m_img_flip[:3, -1] = (5., 10., 12.)
                dist = abs(np.linalg.norm(p_old_pre - np.asarray(m_img_flip[:3, -1])))
                p_old_pre = m_img_flip[:3, -1].copy()

                # Uncertanity visualization  --
                # each tract branch is computed with one set of parameters ajusted from 1 to 10
                n_param = 1 + (n_branches % 10)
                # rescale the alpha value that defines the opacity of the branch
                # the n interval is [1, 10] and the new interval is [51, 255]
                # the new interval is defined to have no 0 opacity (minimum is 51, i.e., 20%)
                alpha = (n_param - 1) * (255 - 51) / (10 - 1) + 51
                trekker.minFODamp(n_param * 0.01)
                trekker.dataSupportExponent(n_param * 0.1)
                # ---

                # When moving the coil further than the seed_radius restart the bundle computation
                # Currently, it stops to compute tracts when the maximum number of tracts is reached maybe keep
                # computing even if reaches the maximum
                if dist >= dist_radius:
                    # Anatomic constrained seed computation ---
                    # The original seed location is replaced by the gray-white  matter interface that is closest to
                    # the coil center
                    try:
                        #TODO: Create a dialog error to say when the ACT data is not loaded and prevent
                        # the interface from freezing. Give the user a chance to load it (maybe in task_navigator)
                        coord_list_w_tr = m_img_flip @ self.coord_list_w
                        coord_offset = grid_offset(act_data, coord_list_w_tr, img_shift)
                    except IndexError:
                        # This error might be caused by the coordinate exceeding the image array dimensions.
                        # Needs further verification.
                        coord_offset = None
                    # ---

                    # Translate the coordinate along the normal vector of the object/coil ---
                    if coord_offset is None:
                        # apply the coil transformation matrix
                        coord_offset = m_img_flip[:3, -1] - offset * m_img_flip[:3, 2]
                    # ---

                    # convert the world coordinates to the voxel space for using as a seed in Trekker
                    # seed_trk.shape == [1, 3]
                    seed_trk = img_utils.convert_world_to_voxel(coord_offset, affine)
                    # print("Desired: {}".format(seed_trk.shape))

                    # DEBUG: uncomment the seed_trk below
                    # Juuso's
                    # seed_trk = np.array([[-8.49, -8.39, 2.5]])
                    # Baran M1
                    # seed_trk = np.array([[27.53, -77.37, 46.42]])
                    # print("Given: {}".format(seed_trk.shape))
                    # print("Seed: {}".format(seed))

                    # Spherical sampling of seed coordinates ---
                    if sph_sampling:
                        # CHECK: We use ACT only for the origin seed, but not for all the other coordinates.
                        # Check how this can be solved. Applying ACT to all coordinates is maybe too much.
                        # Maybe it doesn't matter because the ACT is just to help finding the closest location to
                        # the TMS coil center. Also, note that the spherical sampling is applied only when the coil
                        # location changes, all further iterations used the fixed n_threads samples to compute the
                        # remaining tracts.

                        # samples = np.random.choice(self.sph_idx, size=n_threads, p=self.pdf)
                        # m_seed[:-1, -1] = seed_trk
                        # sph_seed = m_seed @ self.coord_list_sph
                        # seed_trk_r = sph_seed[samples, :]
                        samples = np.random.choice(coord_list_sph.shape[1], size=n_threads)
                        m_seed[:-1, -1] = seed_trk
                        seed_trk_r = m_seed @ coord_list_sph[:, samples]
                        seed_trk_r = seed_trk_r[:-1, :].T
                    else:
                        # set the seeds for trekker, one seed is repeated n_threads times
                        # shape (24, 3)
                        seed_trk_r = np.repeat(seed_trk, n_threads, axis=0)

                    # ---

                    # Trekker has internal multiprocessing approach done in C. Here the number of available threads is
                    # given, but in case a large number of tracts is requested, it will compute all in parallel
                    # automatically for a more fluent navigation, better to compute the maximum number the computer
                    # handles
                    trekker.seed_coordinates(seed_trk_r)
                    # trekker.seed_coordinates(np.repeat(seed_trk, n_threads, axis=0))

                    # run the trekker, this is the slowest line of code, be careful to just use once!
                    trk_list = trekker.run()

                    # check if any tract was found, otherwise doesn't count
                    if trk_list:
                        bundle = vtk.vtkMultiBlockDataSet()
                        branch = tracts_computation_branch(trk_list, alpha)
                        bundle.SetBlock(n_branches, branch)
                        n_branches = 1
                        n_tracts = branch.GetNumberOfBlocks()
                    else:
                        bundle = None
                        n_branches = 0
                        n_tracts = 0

                elif dist < dist_radius and n_tracts < n_tracts_total:
                    trk_list = trekker.run()
                    if trk_list:
                        # compute tract blocks and add to bundle until reaches the maximum number of tracts
                        # the alpha changes depending on the parameter set
                        branch = tracts_computation_branch(trk_list, alpha)
                        if bundle:
                            bundle.SetBlock(n_branches, branch)
                            n_tracts += branch.GetNumberOfBlocks()
                            n_branches += 1
                        else:
                            bundle = vtk.vtkMultiBlockDataSet()
                            bundle.SetBlock(n_branches, branch)
                            n_branches = 1
                            n_tracts = branch.GetNumberOfBlocks()
                    # else:
                    #     bundle = None

                # else:
                #     bundle = None

                # rethink if this should be inside the if condition, it may lock the thread if no tracts are found
                # use no wait to ensure maximum speed and avoid visualizing old tracts in the queue, this might
                # be more evident in slow computer or for heavier tract computations, it is better slow update
                # than visualizing old data
                # self.visualization_queue.put_nowait([coord, m_img, bundle])
                self.tracts_queue.put_nowait((bundle, affine_vtk, coord_offset))
                # print('ComputeTractsThread: put {}'.format(count))

                self.coord_tracts_queue.task_done()
                # self.coord_queue.task_done()
                # print('ComputeTractsThread: done {}'.format(count))

                # sleep required to prevent user interface from being unresponsive
                time.sleep(self.sle)
            # if no coordinates pass
            except queue.Empty:
                # print("Empty queue in tractography")
                pass
            # if queue is full mark as done (may not be needed in this new "nowait" method)
            except queue.Full:
                # self.coord_queue.task_done()
                self.coord_tracts_queue.task_done()


class ComputeTractsThreadSingleBlock(threading.Thread):

    def __init__(self, inp, affine_vtk, coord_queue, visualization_queue, event, sle):
        """Class (threading) to compute real time tractography data for visualization in a single loop.

        Different than ComputeTractsThread because it does not keep adding tracts to the bundle until maximum,
        is reached. It actually compute all requested tracts at once. (Might be deleted in the future)!
        Tracts are computed using the Trekker library by Baran Aydogan (https://dmritrekker.github.io/)
        For VTK visualization, each tract (fiber) is a constructed as a tube and many tubes combined in one
        vtkMultiBlockDataSet named as a branch. Several branches are combined in another vtkMultiBlockDataSet named as
        bundle, to obtain fast computation and visualization. The bundle dataset is mapped to a single vtkActor.
        Mapper and Actor are computer in the data/viewer_volume.py module for easier handling in the invesalius 3D scene.

        Sleep function in run method is used to avoid blocking GUI and more fluent, real-time navigation

        :param inp: List of inputs: trekker instance, affine numpy array, seed_offset, seed_radius, n_threads
        :type inp: list
        :param affine_vtk: Affine matrix in vtkMatrix4x4 instance to update objects position in 3D scene
        :type affine_vtk: vtkMatrix4x4
        :param coord_queue: Queue instance that manage coordinates read from tracking device and coregistered
        :type coord_queue: queue.Queue
        :param visualization_queue: Queue instance that manage coordinates to be visualized
        :type visualization_queue: queue.Queue
        :param event: Threading event to coordinate when tasks as done and allow UI release
        :type event: threading.Event
        :param sle: Sleep pause in seconds
        :type sle: float
        """

        threading.Thread.__init__(self, name='ComputeTractsThread')
        self.inp = inp
        self.affine_vtk = affine_vtk
        self.coord_queue = coord_queue
        self.visualization_queue = visualization_queue
        self.event = event
        self.sle = sle

    def run(self):

        trekker, affine, offset, n_tracts_total, seed_radius, n_threads = self.inp
        # as a single block, computes all the maximum number of tracts at once, not optimal for navigation
        n_threads = n_tracts_total
        p_old = np.array([[0., 0., 0.]])
        root = vtk.vtkMultiBlockDataSet()

        # Compute the tracts
        # print('ComputeTractsThread: event {}'.format(self.event.is_set()))
        while not self.event.is_set():
            try:
                coord, m_img, m_img_flip = self.coord_queue.get_nowait()

                # translate the coordinate along the normal vector of the object/coil
                coord_offset = m_img_flip[:3, -1] - offset * m_img_flip[:3, 2]
                # coord_offset = np.array([[27.53, -77.37, 46.42]])
                dist = abs(np.linalg.norm(p_old - np.asarray(coord_offset)))
                p_old = coord_offset.copy()
                seed_trk = img_utils.convert_world_to_voxel(coord_offset, affine)
                # Juuso's
                # seed_trk = np.array([[-8.49, -8.39, 2.5]])
                # Baran M1
                # seed_trk = np.array([[27.53, -77.37, 46.42]])
                # print("Seed: {}".format(seed))

                # set the seeds for trekker, one seed is repeated n_threads times
                # trekker has internal multiprocessing approach done in C. Here the number of available threads is give,
                # but in case a large number of tracts is requested, it will compute all in parallel automatically
                # for a more fluent navigation, better to compute the maximum number the computer handles
                trekker.seed_coordinates(np.repeat(seed_trk, n_threads, axis=0))
                # run the trekker, this is the slowest line of code, be careful to just use once!
                trk_list = trekker.run()

                if trk_list:
                    # if the seed is outside the defined radius, restart the bundle computation
                    if dist >= seed_radius:
                        root = tracts_computation(trk_list, root, 0)
                    self.visualization_queue.put_nowait((coord, m_img, root))

                self.coord_queue.task_done()
                time.sleep(self.sle)
            except queue.Empty:
                pass
            except queue.Full:
                self.coord_queue.task_done()


def set_trekker_parameters(trekker, params):
    """Set all user-defined parameters for tractography computation using the Trekker library

    :param trekker: Trekker instance
    :type trekker: Trekker.T
    :param params: Dictionary containing the parameters values to set in Trekker. Initial values are in constants.py
    :type params: dict
    :return: List containing the Trekker instance and number of threads for parallel processing in the computer
    :rtype: list
    """
    trekker.seed_maxTrials(params['seed_max'])
    # trekker.stepSize(params['step_size'])
    trekker.minFODamp(params['min_fod'])
    # trekker.probeQuality(params['probe_quality'])
    # trekker.maxEstInterval(params['max_interval'])
    # trekker.minRadiusOfCurvature(params['min_radius_curv'])
    # trekker.probeLength(params['probe_length'])
    trekker.writeInterval(params['write_interval'])
    trekker.maxLength(params['max_lenth'])
    trekker.minLength(params['min_lenth'])
    trekker.maxSamplingPerStep(params['max_sampling_step'])

    # check number if number of cores is valid in configuration file,
    # otherwise use the maximum number of threads which is usually 2*N_CPUS
    n_threads = 2 * const.N_CPU
    if isinstance((params['numb_threads']), int) and params['numb_threads'] <= 2*const.N_CPU:
        n_threads = params['numb_threads']

    trekker.numberOfThreads(n_threads)
    # print("Trekker config updated: n_threads, {}; seed_max, {}".format(n_threads, params['seed_max']))
    return trekker, n_threads


def grid_offset(data, coord_list_w_tr, img_shift):
    # convert to int so coordinates can be used as indices in the MRI image space
    coord_list_w_tr_mri = coord_list_w_tr[:3, :].T.astype(int) + np.array([[0, img_shift, 0]])

    #FIX: IndexError: index 269 is out of bounds for axis 2 with size 256
    # error occurs when running line "labs = data[coord..."
    # need to check why there is a coordinate outside the MRI bounds

    # extract the first occurrence of a specific label from the MRI image
    labs = data[coord_list_w_tr_mri[..., 0], coord_list_w_tr_mri[..., 1], coord_list_w_tr_mri[..., 2]]
    lab_first = np.argmax(labs == 1)
    if labs[lab_first] == 1:
        pt_found = coord_list_w_tr_mri[lab_first, :]
        # convert coordinate back to invesalius 3D space
        pt_found_inv = pt_found - np.array([0., img_shift, 0.])
    else:
        pt_found_inv = None

    # # convert to world coordinate space to use as seed for fiber tracking
    # pt_found_tr = np.append(pt_found, 1)[np.newaxis, :].T
    # # default affine in invesalius is actually the affine inverse
    # pt_found_tr = np.linalg.inv(affine) @ pt_found_tr
    # pt_found_tr = pt_found_tr[:3, 0, np.newaxis].T

    return pt_found_inv
