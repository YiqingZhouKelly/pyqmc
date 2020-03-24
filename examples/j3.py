if __name__ == '__main__':
    import pyscf
    import pyqmc

    mol = pyscf.gto.M(atom="H 0. 0. 0.; H 0. 0. 1.5", basis="cc-pvtz", unit="bohr")
    mf = pyscf.scf.RHF(mol).run()
    slater_jastrow_wf = pyqmc.slater_jastrow(mol, mf)
    j3 = pyqmc.manybody_jastrow.J3(mol)
    wf = pyqmc.multiplywf.MultiplyWF(*slater_jastrow_wf.wf_factors, j3)
