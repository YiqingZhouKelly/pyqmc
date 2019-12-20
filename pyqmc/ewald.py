import numpy as np
import pyqmc
from scipy.special import erfc


class Ewald:
    r"""
    The Ewald summation is a scheme for computing the Coulomb energy of a periodic arrangement of charges.
    The Couomb energy is the sum of two divergent quantities: sum of interactions between same-charge pairs, and opposite-charge pairs.
    Separated differently, the divergence can be avoided: the sum is divided into real space (short range) and reciprocal space (long range) parts, each of which converges quickly.
    The separation is determined by the parameter $\alpha$

    The Ewald separation is:

    .. math:: E_{Coulomb} = E_{\rm real space} + E_{\rm reciprocal space} - E_{\rm intra-cell} - E_{\rm self} - E_{\rm charged system}

    .. math:: E_{\rm real space} = \frac{1}{2} {\sum_{\vec{n}}}^\dagger \sum_{i=1}^N \sum_{j=1}^N q_i q_j \frac{\erfc(\alpha |\vec{x}_ij+\vec{n}|)}{|\vec{x}_ij+\vec{n}|}

    .. math:: E_{\rm reciprocal space} = \frac{4\pi}{V} \frac{1}{2} \sum_{k \ne 0} \frac{1}{k^2} e^{-\frac{k^2}{4\alpha^2}} \left| \sum_{i=1}^N q_i e^{-i\vec{k}\cdot\vec{x}_i} \right|^2

    .. math:: E_{\rm self}  = \frac{\alpha}{\sqrt{\pi}} \sum_{i=1}^N q_i^2

    .. math:: E_{\rm charged}  = \frac{\pi}{2V\alpha^2} \left| \sum_{i=1}^N q_i \right|^2

    In our implementation, the parts are further split into electron-electron, electron-ion, and ion-ion contributions. Since the ions don't move in our calculations, the ion-ion term only needs to be computed once.

    Real space terms:

    .. math:: E_{\rm real space}^{ii} = \frac{1}{2} {\sum_{\vec{n}}}^\dagger \sum_{I=1}^{N_i} \sum_{J=1}^{N_i} Z_I Z_J \frac{\erfc(\alpha |\vec{x}_{IJ}+\vec{n}|)}{|\vec{x}_{IJ}+\vec{n}|}

    .. math:: E_{\rm real space}^{ee} = \frac{1}{2} {\sum_{\vec{n}}}^\dagger \sum_{i=1}^{N_e} \sum_{j=1}^{N_e} \frac{\erfc(\alpha |\vec{x}_ij+\vec{n}|)}{|\vec{x}_ij+\vec{n}|}

    .. math:: E_{\rm real space}^{ei} = {\sum_{\vec{n}}} \sum_{i=1}^{N_e} \sum_{I=1}^{N_i} Z_I \frac{\erfc(\alpha |\vec{x}_iI+\vec{n}|)}{|\vec{x}_iI+\vec{n}|}

    Reciprocal space terms:

    .. math:: E_{\rm reciprocal space}^{ii} = \frac{4\pi}{V} \frac{1}{2} \sum_{k \ne 0} \frac{1}{k^2} e^{-\frac{k^2}{4\alpha^2}} \left| \sum_{I=1}^{N_i} Z_I e^{-i\vec{k}\cdot\vec{x}_I} \right|^2

    .. math:: E_{\rm reciprocal space}^{ee} = - \frac{4\pi}{V} \frac{1}{2} \sum_{k \ne 0} \frac{1}{k^2} e^{-\frac{k^2}{4\alpha^2}} \left| \sum_{i=1}^{N_e} e^{-i\vec{k}\cdot\vec{x}_i} \right|^2

    .. math:: E_{\rm reciprocal space}^{ei} = \frac{4\pi}{V} \frac{1}{2} \sum_{k \ne 0} \frac{1}{k^2} e^{-\frac{k^2}{4\alpha^2}} {\rm Re} \left[ 2 \sum_{i=1}^{N_e} \sum_{I=1}^{N_i} -Z_I e^{-i\vec{k}\cdot\vec{x}_i} e^{i\vec{k}\cdot\vec{x}_I} \right]

    Self energy:

    .. math:: E_{\rm self}^{e} = - \frac{\alpha N_e}{\sqrt{\pi}}

    .. math:: E_{\rm self}^{i} = - \frac{\alpha}{\sqrt{\pi}} \sum_{I=1}^{N_i} Z_I^2

    Charged system energy:
    
    .. math:: E_{\rm charged}^{ee} = - \frac{\pi}{2V\alpha^2} N_e^2

    .. math:: E_{\rm charged}^{ei} =   \frac{\pi}{2V\alpha^2} 2 N_e \sum_{I=1}^{N_i} Z_I

    .. math:: E_{\rm charged}^{ii} = - \frac{\pi}{2V\alpha^2} \left[ \sum_{I=1}^{N_i} Z_I^2 + 2 \sum_{I<J}^{N_i} Z_I Z_J \right]

    """

    def __init__(self, cell, ewald_gmax=200, nlatvec=2):
        """
        Class for computing Ewald sums. The sum is split into real space (short range) and reciprocal space (long range) terms; the electron-electron, electron-ion, and ion-ion contributions are computed separately.
        Inputs:
            cell: pyscf Cell object (simulation cell)
            ewald_gmax: int, how far to take reciprocal sum; probably never needs to be changed.
            nlatvec: int, how far to take real space sum; probably never needs to be changed.
        """
        self.nelec = np.array(cell.nelec)
        self.atom_coords, self.atom_charges = cell.atom_coords(), cell.atom_charges()
        self.latvec = cell.lattice_vectors()
        self.set_lattice_displacements(nlatvec)
        self.set_up_reciprocal_ewald_sum(ewald_gmax)

    def set_lattice_displacements(self, nlatvec):
        """
        Generates list of lattice-vector displacements to add together for real space sum
        """
        XYZ = np.meshgrid(*[np.arange(-nlatvec, nlatvec + 1)] * 3, indexing="ij")
        xyz = np.stack(XYZ, axis=-1).reshape((-1, 3))
        self.lattice_displacements = np.dot(xyz, self.latvec)

    def set_up_reciprocal_ewald_sum(self, ewald_gmax):
        r"""
        Determine parameters for Ewald sums. 

        $\alpha$ determines the partitioning of the real and reciprocal space parts.

        We define a weight `gweight` for the part of the reciprocal space sums that doesn't depend on the coordinates:
        
        .. math:: W_G = \frac{4\pi}{V |\vec{G}|^2} e^{- \frac{|\vec{G}|^2}{ 4\alpha^2}}

        Inputs:
            latvec: (3, 3) array of lattice vectors; latvec[0] is the first
            ewald_gmax: int, max number of reciprocal lattice vectors to check away from 0
        """
        cellvolume = np.linalg.det(self.latvec)
        recvec = np.linalg.inv(self.latvec)
        crossproduct = recvec.T * cellvolume

        # Determine alpha
        tmpheight_i = np.einsum("ij,ij->i", crossproduct, self.latvec)
        length_i = np.linalg.norm(crossproduct, axis=1)
        smallestheight = np.amin(np.abs(tmpheight_i) / length_i)
        self.alpha = 5.0 / smallestheight
        print("Setting Ewald alpha to ", self.alpha)

        # Determine G points to include in reciprocal Ewald sum
        XYZ = np.meshgrid(*[np.arange(-ewald_gmax, ewald_gmax + 1)] * 3, indexing="ij")
        X, Y, Z = [x.ravel() for x in XYZ]
        positive_octants = X + 1e-6 * Y + 1e-12 * Z > 0  # assume ewald_gmax < 1e5
        gpoints = np.stack((X, Y, Z), axis=-1)[positive_octants]
        gpoints = np.dot(gpoints, recvec) * 2 * np.pi
        gsquared = np.sum(gpoints ** 2, axis=1)
        gweight = 4 * np.pi * np.exp(-gsquared / (4 * self.alpha ** 2))
        gweight /= cellvolume * gsquared
        bigweight = gweight > 1e-10
        self.gpoints = gpoints[bigweight]
        self.gweight = gweight[bigweight]

        self.set_ewald_constants(cellvolume)

    def set_ewald_constants(self, cellvolume):
        r"""
        Compute Ewald constants (independent of particle positions): self energy and charged system energy. Here we compute the combined terms. These terms are independent of the convergence parameters `gmax` and `nlatvec`, but do depend on the partitioning parameter $\alpha$.
        
        We define two constants, `squareconst`, the coefficient of the squared charges, 
        and `ijconst`, the coefficient of the pairs:

        .. math:: C_{ij} = - \frac{\pi}{V\alpha^2}

        .. math:: C_{\rm square} = - \frac{\alpha}{\sqrt{\pi}}  - \frac{\pi}{2V\alpha^2} 
                  = - \frac{\alpha}{\sqrt{\pi}}  - \frac{C_{ij}}{2}

        The Ewald object doesn't retain information about the configurations, including number of electrons, so the electron constants are defined as functions of $N_e$.


        Self plus charged-system energy:
        
        .. math:: E_{\rm self+charged}^{ee} = N_e C_{\rm square} + \frac{N_e(N_e-1)}{2} C_{ij}

        .. math:: E_{\rm self+charged}^{ei} = N_e \sum_{I=1}^{N_i} Z_I C_{ij}

        .. math:: E_{\rm self+charged}^{ii} = \sum_{I=1}^{N_i} Z_I^2 C_{\rm square} + \sum_{I<J}^{N_i} Z_I Z_J C_{ij}

        We also compute contributions from a single electron, to separate the Ewald sum by electron.
        
        .. math:: E_{\rm self+charged}^{\rm single} = C_{\rm square} + \frac{N_e-1}{2} C_{ij} - \sum_{I=1}^{N_i} Z_I C_{ij}

        .. math:: E_{\rm self+charged}^{\rm single-test} = C_{\rm square} - \sum_{I=1}^{N_i} Z_I C_{ij}

        """
        i_sum = np.sum(self.atom_charges)
        ii_sum2 = np.sum(self.atom_charges ** 2)
        ii_sum = (i_sum ** 2 - ii_sum2) / 2

        ijconst = -np.pi / (cellvolume * self.alpha ** 2)
        self.ijconst = ijconst
        squareconst = -self.alpha / np.sqrt(np.pi) + ijconst / 2

        self.ii_const = ii_sum * ijconst + ii_sum2 * squareconst
        self.ee_const = lambda ne: ne * (ne - 1) / 2 * ijconst + ne * squareconst
        self.ei_const = lambda ne: -ne * i_sum * ijconst

        self.e_single = lambda ne: (ne - 1) * ijconst - i_sum * ijconst + squareconst
        self.e_single_test = -i_sum * ijconst + squareconst
        self.ion_ion = self.ewald_ion()

        # XC correction not used, so we can compare to other codes
        rs = lambda ne: (3 / (4 * np.pi) / (ne * cellvolume)) ** (1 / 3)
        cexc = 0.36
        xc_correction = lambda ne: cexc / rs(ne)

    def ewald_ion(self):
        r"""
        Compute ion contribution to Ewald sums. 
        There is a constant term we ignore, corresponding to the interaction of a particle with its own image in other cells:

        .. math:: C_{\rm ignore}^{\rm ii} = \sum_{\vec{n} \ne 0} \sum_{I=1}^{N_i} Z_I^2  \frac{\erfc(\alpha |\vec{n}|)}{|\vec{n}|} 

        The real space part:

        .. math:: E_{\rm real space}^{ii} = \sum_{\vec{n}} \sum_{I<J}^{N_i} Z_I Z_J \frac{\erfc(\alpha |\vec{x}_{IJ}+\vec{n}|)}{|\vec{x}_{IJ}+\vec{n}|} 
        + C_{\rm ignore}^{\rm ii}

        The reciprocal space part:

        .. math:: E_{\rm reciprocal space}^{i} = \sum_{\vec{G} \in {\rm octant}} W_G \left| \sum_{I=1}^{N_i} e^{-i\vec{G}\cdot\vec{x}_I} \right|^2

        where `gweight` is a factor that doesn't depend on the coordinates:
        
        .. math:: W_G = \frac{4\pi}{V |\vec{G}|^2} e^{- \frac{|\vec{G}|^2}{ 4\alpha^2}}

        Returns:
            ion_ion: float, ion-ion component of Ewald sum
        """
        # Real space part
        if len(self.atom_charges) == 1:
            ion_ion_real = 0
        else:
            dist = pyqmc.distance.MinimalImageDistance(self.latvec)
            ion_distances, ion_inds = dist.dist_matrix(self.atom_coords[np.newaxis])
            rvec = ion_distances[:, :, np.newaxis, :] + self.lattice_displacements
            r = np.linalg.norm(rvec, axis=-1)
            charge_ij = np.prod(self.atom_charges[np.asarray(ion_inds)], axis=1)
            ion_ion_real = np.einsum("j,ijk->", charge_ij, erfc(self.alpha * r) / r)

        # Reciprocal space part
        GdotR = np.dot(self.gpoints, self.atom_coords.T)
        self.ion_exp = np.dot(np.exp(1j * GdotR), self.atom_charges)
        ion_ion_rec = np.dot(self.gweight, np.abs(self.ion_exp) ** 2)

        ion_ion = ion_ion_real + ion_ion_rec
        return ion_ion

    def ewald_electron(self, configs):
        r"""
        Compute the Ewald sum for e-e and e-ion

        For ease of notation (and reading the code), define

        ..math:: r_{iIn} = |\vec{x}_iI+\vec{n}|

        ..math:: r_{ijn} = |\vec{x}_ij+\vec{n}|

        As with the ions, we ignore a constant term in the real-space e-e sum corresponding to the interaction of a particle with its own image in other cells:

        .. math:: C_{\rm ignore}^{\rm ee} = \sum_{\vec{n} \ne 0} \sum_{i=1}^{N_e} \frac{\erfc(\alpha |\vec{n}|)}{|\vec{n}|} 


        Real space e-e:
        .. math:: E_{\rm real space}^{ee} = \sum_{\vec{n}} \sum_{i<j}^{N_e} \frac{\erfc(\alpha r_{ijn})}{r_{ijn}}
        + C_{\rm ignore}^{\rm ee}

        Real space e-i:

        .. math:: E_{\rm real space}^{ei} = {\sum_{\vec{n}}} \sum_{i=1}^{N_e} \sum_{I=1}^{N_i} Z_I \frac{\erfc(\alpha r_{iIn})}{r_{iIn}}

        Reciprocal space e-e:

        .. math:: E_{\rm reciprocal space}^{ee} = \sum_{\vec{G} \in {\rm octant}} W_G \left| \sum_{i=1}^{N_e} e^{-i\vec{k}\cdot\vec{x}_i} \right|^2

        Reciprocal space e-i:

        .. math:: E_{\rm reciprocal space}^{ei} = \sum_{\vec{G} \in {\rm octant}} W_G {\rm Re} \left[ 2 \sum_{i=1}^{N_e} \sum_{I=1}^{N_i} -Z_I e^{-i\vec{k}\cdot\vec{x}_i} e^{i\vec{k}\cdot\vec{x}_I} \right]

        where `gweight` is a factor that doesn't depend on the coordinates:
        
        .. math:: W_G = \frac{4\pi}{V |\vec{G}|^2} e^{- \frac{|\vec{G}|^2}{ 4\alpha^2}}


        Inputs:
            configs: pyqmc PeriodicConfigs object of shape (nconf, nelec, ndim)
        Returns:
            ee: electron-electron part
            ei: electron-ion part
        """
        nconf, nelec, ndim = configs.configs.shape

        # Real space electron-ion part
        # ei_distances shape (elec, conf, atom, dim)
        ei_distances = configs.dist.dist_i(self.atom_coords, configs.configs)
        rvec = ei_distances[:, :, :, np.newaxis, :] + self.lattice_displacements
        r = np.linalg.norm(rvec, axis=-1)
        ei_real_separated = np.einsum(
            "k,ijkl->ji", -self.atom_charges, erfc(self.alpha * r) / r
        )

        # Real space electron-electron part
        if nelec > 1:
            ee_distances, ee_inds = configs.dist.dist_matrix(configs.configs)
            rvec = ee_distances[:, :, np.newaxis, :] + self.lattice_displacements
            r = np.linalg.norm(rvec, axis=-1)
            ee_cij = np.sum(erfc(self.alpha * r) / r, axis=-1)

            ee_matrix = np.zeros((nconf, nelec, nelec))
            # ee_matrix[:, ee_inds] = ee_cij
            for ((i, j), val) in zip(ee_inds, ee_cij.T):
                ee_matrix[:, i, j] = val
                ee_matrix[:, j, i] = val
            ee_real_separated = ee_matrix.sum(axis=-1) / 2
        else:
            ee_real_separated = np.zeros(nelec)

        # Reciprocal space electron-electron part
        e_GdotR = np.dot(configs.configs, self.gpoints.T)
        e_expGdotR = np.exp(1j * e_GdotR)
        sum_e_exp = np.sum(e_expGdotR, axis=1, keepdims=True)
        coscos_sinsin = np.real(sum_e_exp.conj() * e_expGdotR)
        ### Don't know why we subtract 0.5 for "separated"
        ee_recip_separated = np.dot(coscos_sinsin - 0.5, self.gweight)

        # Reciprocal space electron-ion part
        coscos_sinsin = np.real(-self.ion_exp.conj() * e_expGdotR)
        ei_recip_separated = np.dot(coscos_sinsin, self.gweight)

        # Combine parts
        self.ei_separated = ei_real_separated + 2 * ei_recip_separated
        self.ee_separated = ee_real_separated + 1 * ee_recip_separated
        self.ewalde_separated = self.ei_separated + self.ee_separated
        nelec = ee_recip_separated.shape[1]
        ### Add back the 0.5 that was subtracted earlier
        ee = self.ee_separated.sum(axis=1) + nelec / 2 * self.gweight.sum()
        ei = self.ei_separated.sum(axis=1)
        return ee, ei

    def energy(self, configs):
        """
        Compute Coulomb energy for a set of configs.  
        
        Inputs:
            configs: pyqmc PeriodicConfigs object of shape (nconf, nelec, ndim)
        Returns: 
            ee: electron-electron part
            ei: electron-ion part
            ii: ion-ion part
        """
        nelec = configs.configs.shape[1]
        ee, ei = self.ewald_electron(configs)
        ee += self.ee_const(nelec)
        ei += self.ei_const(nelec)
        ii = self.ion_ion + self.ii_const
        return ee, ei, ii

    def energy_separated(self, configs):
        """
        Compute Coulomb energy separated by electron in a set of configs. 
        NOTE: energy() needs to be called first to update the separated energy values
        Inputs:
            configs: pyqmc PeriodicConfigs object of shape (nconf, nelec, ndim)
        Returns: 
            (nelec,) energies
        """
        nelec = configs.configs.shape[1]
        return self.e_single(nelec) + self.ewalde_separated

    def energy_with_test_pos(self, configs, epos):
        """
        Compute Coulomb energy of an additional test electron with a set of configs
        Inputs:
            configs: pyqmc PeriodicConfigs object of shape (nconf, nelec, ndim)
            epos: pyqmc PeriodicConfigs object of shape (nconf, ndim)
        Returns: 
            Vtest: (nconf, nelec+1) array. The first nelec columns are Coulomb energies between the test electron and each electron; the last column is the contribution from all the ions.
        """
        nconf, nelec, ndim = configs.configs.shape
        Vtest = np.zeros((nconf, nelec + 1)) + self.ijconst
        Vtest[:, -1] = self.e_single_test

        # Real space electron-ion part
        # ei_distances shape (conf, atom, dim)
        ei_distances = configs.dist.dist_i(self.atom_coords, epos.configs)
        rvec = ei_distances[:, :, np.newaxis, :] + self.lattice_displacements
        r = np.linalg.norm(rvec, axis=-1)
        Vtest[:, -1] += np.einsum(
            "k,jkl->j", -self.atom_charges, erfc(self.alpha * r) / r
        )

        # Real space electron-electron part
        ee_distances = configs.dist.dist_i(configs.configs, epos.configs)
        rvec = ee_distances[:, :, np.newaxis, :] + self.lattice_displacements
        r = np.linalg.norm(rvec, axis=-1)
        Vtest[:, :-1] += np.sum(erfc(self.alpha * r) / r, axis=-1)

        # Reciprocal space electron-electron part
        e_expGdotR = np.exp(1j * np.dot(configs.configs, self.gpoints.T))
        test_exp = np.exp(1j * np.dot(epos.configs, self.gpoints.T))
        ee_recip_separated = np.dot(np.real(test_exp.conj() * e_expGdotR), self.gweight)
        Vtest[:, :-1] += 2 * ee_recip_separated

        # Reciprocal space electrin-ion part
        coscos_sinsin = np.real(-self.ion_exp.conj() * test_exp)
        ei_recip_separated = np.dot(coscos_sinsin + 0.5, self.gweight)
        Vtest[:, -1] += 2 * ei_recip_separated

        return Vtest
