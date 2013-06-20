import unittest
import sympy as smp
import numpy as np
import scipy.sparse as sps
from dolfin import *

# unittests for the suite
# if not specified otherwise we use the unit square 
# with 0-Dirichlet BCs with a known solution 

class OptConPyFunctions(unittest.TestCase):

    def setUp(self):

        self.mesh = UnitSquareMesh(24, 24)
        self.V = VectorFunctionSpace(self.mesh, "CG", 2)
        self.Q = FunctionSpace(self.mesh, "CG", 1)
        self.nu = 1e-5
        
        x, y, t, nu, om = smp.symbols('x,y,t,nu,om')
        ft = smp.sin(om*t)
        u_x = ft*x*x*(1-x)*(1-x)*2*y*(1-y)*(2*y-1)
        u_y = ft*y*y*(1-y)*(1-y)*2*x*(1-x)*(1-2*x)
        p = ft*x*(1-x)*y*(1-y)

        # div u --- should be zero!!
        self.assertEqual(smp.simplify(smp.diff(u_x,x) + smp.diff(u_y,y)), 0)

        self.u_x = u_x
        self.u_y = u_y
        self.p = p

        def sympy2expression(term):
            '''Translate a SymPy expression to a FEniCS expression string.
               '''
               # This is somewhat ugly: 
               # First replace the variables r, z, by something
               # that probably doesn't appear anywhere else, 
               # e.g., RRR, ZZZ, then
               # convert this into a string, 
               # and then replace the substrings RRR, ZZZ
               # by x[0], x[1], respectively.
            exp = smp.printing.ccode(term.subs('x','XXX').subs('y','YYY')) \
                .replace('M_PI','pi') \
                .replace('XXX','x[0]').replace('YYY','x[1]')
            return exp

        dotu_x = smp.simplify(smp.diff(u_x,t))
        dotu_y = smp.simplify(smp.diff(u_y,t))

        diffu_x = smp.simplify(nu*(smp.diff(u_x,x,x) + smp.diff(u_x,y,y)))
        diffu_y = smp.simplify(nu*(smp.diff(u_y,x,x) + smp.diff(u_y,y,y)))

        dp_x = smp.simplify( smp.diff(p,x) )
        dp_y = smp.simplify( smp.diff(p,y) )

        adv_x = smp.simplify( u_x*smp.diff(u_x,x) + u_y*smp.diff(u_x,y) )
        adv_y = smp.simplify( u_x*smp.diff(u_y,x) + u_y*smp.diff(u_y,y) )

        self.F = Expression(('0','0'))
        a = sympy2expression(u_x)
        b = sympy2expression(u_y)
        self.F = Expression(('0','0'))
        
        self.fenics_sol_u = Expression((a, b), t=0.0, om=1.0)
    
    def test_linearized_mat_NSE_form(self):
        """check the conversion: dolfin form <-> numpy arrays

          and the linearizations"""

        import dolfin_to_nparrays as dtn

        u = self.fenics_sol_u
        u.t = 1.0
        ufun = project(u, self.V)
        uvec = ufun.vector().array().reshape(len(ufun.vector()), 1)

        N1, N2, fv = dtn.get_convmats(u0_dolfun=ufun, V=self.V)
        conv = dtn.get_convvec(u0_dolfun=ufun, V=self.V)

        self.assertTrue(np.allclose(conv, N1*uvec))
        self.assertTrue(np.allclose(conv, N2*uvec))

    def test_expand_condense_vfuncs(self):
        """check the expansion of vectors to dolfin funcs

        """
        from dolfin_to_nparrays import expand_vp_dolfunc

        u = Expression(('x[1]','0'))
        ufun = project(u, self.V, solver_type='lu')
        uvec = ufun.vector().array().reshape(len(ufun.vector()), 1)

        # Boundaries
        def top(x, on_boundary): 
            return x[1] > 1.0 - DOLFIN_EPS 
        def leftbotright(x, on_boundary): 
            return ( x[0] > 1.0 - DOLFIN_EPS 
                or x[1] < DOLFIN_EPS 
                or x[0] < DOLFIN_EPS)

        # No-slip boundary condition for velocity
        noslip = u 
        bc0 = DirichletBC(self.V, noslip, leftbotright)
        # Boundary condition for velocity at the lid
        lid = u 
        bc1 = DirichletBC(self.V, lid, top)
        # Collect boundary conditions
        diribcs = [bc0, bc1]
        bcinds = []
        for bc in diribcs:
            bcdict = bc.get_boundary_values()
            bcinds.extend(bcdict.keys())
            
        # indices of the innernodes
        innerinds = np.setdiff1d(range(self.V.dim()), 
                                        bcinds).astype(np.int32)

        # take only the inner nodes
        uvec_condensed = uvec[innerinds,]

        v, p = expand_vp_dolfunc(V=self.V, vc=uvec_condensed,
                invinds=innerinds, diribcs=diribcs) 

        vvec = v.vector().array().reshape(len(v.vector()), 1)

        self.assertTrue(np.allclose(uvec, vvec))

    def test_inout_opas(self):
        import scipy.sparse.linalg as spsla
        import cont_obs_utils as cou
        parameters.linear_algebra_backend = "uBLAS"

        N = 40
        mesh = UnitSquareMesh(N, N)
        V = VectorFunctionSpace(mesh, "CG", 2)

        NU, NY = 12, 7

        cdcoo = dict(xmin=0.4,
                     xmax=0.6,
                     ymin=0.2,
                     ymax=0.3)

        odcoo = dict(xmin=0.45,
                     xmax=0.55,
                     ymin=0.6,
                     ymax=0.8)

        cdom = cou.ContDomain(cdcoo)
        odom = cou.ContDomain(odcoo)

        # get mass mat
        v, w = TrialFunction(V), TestFunction(V)
        Mv = assemble(inner(w,v)*dx)
        rows, cols, values = Mv.data()
        Mv = sps.csr_matrix((values, cols, rows))

        # check the B
        B = cou.get_inp_opa(cdom=cdom, V=V, NU=NU) 

        # get mass mat
        v, w = TrialFunction(V), TestFunction(V)
        Mv = assemble(inner(w,v)*dx)
        rows, cols, values = Mv.data()
        Mv = sps.csr_matrix((values, cols, rows))
        Bu = spsla.spsolve(Mv,B*np.vstack([0*np.ones((NU,1)),
                                           1*np.ones((NU,1))]))

        v = Function(V)
        v.vector().set_local(Bu)
        plot(v)

        # check the C

        MyC, My = cou.get_mout_opa(odom=odom, V=V, NY=NY)

        exv = Expression(('1', '0'))
        testv = interpolate(exv, V)

        plot(testv)

        MyCv = MyC*testv.vector().array()
        testy = spsla.spsolve(My,MyCv)

        # print np.linalg.norm(testy)

        ymesh = IntervalMesh(NY-1, odcoo['ymin'], odcoo['ymax'])
        Y = FunctionSpace(ymesh, 'CG', 1)
        yx = Function(Y)
        yy = Function(Y)

        yx.vector().set_local(testy[:NY])
        plot(yx)
        yy.vector().set_local(testy[NY:])
        #plot(yy)
        interactive(True)

if __name__ == '__main__':
    unittest.main()
